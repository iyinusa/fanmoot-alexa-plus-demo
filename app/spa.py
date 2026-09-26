"""Serving the built SPA, and the client-side routing fallback.

The same shape as ``fanmoot``'s ``app/spa.py``, trimmed to one bundle. This
router is registered **last**, so ``/healthz``, ``/ws`` and ``/api/*`` get first
refusal and only what nothing else claimed falls through to the shell. ``/linked``
— where the sign-in popup lands (W1-7) — is an SPA route, so it is served here.

Unmatched paths under ``/api/`` and the asset directories return a real 404
rather than ``index.html``: an API call answered with HTML is a JSON parse
error that looks like a bug in the caller, and a mistyped font URL answered
with a 200 is a silent fallback face that nothing ever reports.

Two things happen on the way out (W5):

- **Compression.** Text files go out gzipped to a browser that accepts it.
  Neither uvicorn nor Traefik compresses by default, and the bundle is about
  three times smaller gzipped, which matters most on a phone at the judging
  table (W5-3). Each file is compressed once per process and kept: the bundle
  does not change while the process runs.
- **The share link's origin.** ``index.html`` carries ``__FMD_PUBLIC_URL__``
  where Open Graph wants an absolute URL (``og:image``, ``og:url``, the
  canonical link). It is replaced with ``FMD_PUBLIC_BASE_URL`` here, so a
  staging deploy's link previews point at staging and the same image serves
  both (W5-6).
"""

from __future__ import annotations

import gzip
import mimetypes
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.settings import get_settings

router = APIRouter(include_in_schema=False)

#: Where `vite build` writes (web/vite.config.ts) and the Docker image copies
#: the bundle to. Module-level so a test can point it at a directory it built.
WEB_DIR = Path(__file__).resolve().parent / "static" / "web"

#: Prefixes the SPA must never answer for.
_NEVER_SPA = ("/api/", "/ws", "/healthz", "/.well-known/", "/oauth/")

#: Directories of real files in the bundle. Missing under one of these is a
#: wrong URL, never an app route.
_ASSET_DIRS = ("/assets/", "/audio/", "/fonts/", "/icons/", "/share/")

#: python:3.12-slim ships without a mime.types database, so these resolve to
#: nothing there and would go out as application/octet-stream — a font some
#: strict Content-Type checks then refuse. Registered so the container behaves
#: like a developer's machine.
for _suffix, _type in {
    ".woff2": "font/woff2",
    ".webp": "image/webp",
    ".mjs": "text/javascript",
    ".webmanifest": "application/manifest+json",
}.items():
    mimetypes.add_type(_type, _suffix)


#: Worth compressing. Fonts and images are compressed already, and gzipping a
#: woff2 again only spends CPU.
_COMPRESSIBLE = frozenset({".js", ".mjs", ".css", ".html", ".svg", ".json", ".map", ".txt"})

#: Below this, gzip's own header outweighs what it saves.
_MIN_COMPRESS_BYTES = 1024

#: Where index.html wants the public origin (web/index.html).
PUBLIC_URL_PLACEHOLDER = "__FMD_PUBLIC_URL__"


def is_built() -> bool:
    return (WEB_DIR / "index.html").is_file()


def _not_built() -> HTMLResponse:
    """What a developer sees running the API before building the frontend."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Fanmoot on Alexa+</title>"
        "<div style='font:16px system-ui;padding:3rem;max-width:40rem'>"
        "<h1>The web bundle is not built</h1>"
        "<p>Run <code>make build-web</code>, or <code>make dev-web</code> for the "
        "Vite dev server on :5174. <code>/healthz</code> and <code>/ws</code> "
        "are unaffected.</p></div>",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _accepts_gzip(request: Request) -> bool:
    return "gzip" in request.headers.get("accept-encoding", "").lower()


@lru_cache(maxsize=128)
def _gzipped(path: Path, mtime_ns: int) -> bytes:
    """One file, compressed once. ``mtime_ns`` is in the key so a rebuild
    under ``make dev`` is not served stale."""
    return gzip.compress(path.read_bytes(), compresslevel=9, mtime=0)


@lru_cache(maxsize=4)
def _shell(path: Path, mtime_ns: int, public_url: str) -> bytes:
    """``index.html`` with the public origin written in."""
    return path.read_text(encoding="utf-8").replace(PUBLIC_URL_PLACEHOLDER, public_url).encode()


def _send(request: Request, body: bytes, media_type: str, cache: str) -> Response:
    headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
    if len(body) >= _MIN_COMPRESS_BYTES and _accepts_gzip(request):
        body = gzip.compress(body, compresslevel=9, mtime=0)
        headers["Content-Encoding"] = "gzip"
    return Response(body, media_type=media_type, headers=headers)


def _file(request: Request, path: Path, cache: str) -> Response:
    if path.suffix in _COMPRESSIBLE and _accepts_gzip(request):
        stat = path.stat()
        if stat.st_size >= _MIN_COMPRESS_BYTES:
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return Response(
                _gzipped(path, stat.st_mtime_ns),
                media_type=media_type,
                headers={
                    "Cache-Control": cache,
                    "Content-Encoding": "gzip",
                    "Vary": "Accept-Encoding",
                },
            )
    return FileResponse(path, headers={"Cache-Control": cache})


# HEAD as well as GET: `@router.get` alone answers HEAD with a 405, and uptime
# monitors and link-preview scrapers ask with HEAD before anything else.
@router.api_route("/{path:path}", methods=["GET", "HEAD"])
async def web_spa(path: str, request: Request) -> Response:
    """The demo shell, for anything no other route claimed."""
    full = f"/{path}"
    if any(full.startswith(prefix) for prefix in _NEVER_SPA):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    if path:
        root = WEB_DIR.resolve()
        candidate = (WEB_DIR / path).resolve()
        # Containment: without it `/../settings.py` would serve source.
        if candidate.is_file() and candidate.is_relative_to(root):
            # Vite content-hashes everything under assets/, so a given URL is
            # the same bytes for ever. Fonts and icons are not hashed.
            cache = (
                "public, max-age=31536000, immutable"
                if full.startswith("/assets/")
                else "public, max-age=86400"
            )
            return _file(request, candidate, cache)
        if any(full.startswith(prefix) for prefix in _ASSET_DIRS):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    index = WEB_DIR / "index.html"
    if not index.is_file():
        return _not_built()
    # The shell names the current hashed bundle, so it is never cached: a
    # cached one pins a judge's browser to a deploy that no longer exists.
    body = _shell(index, index.stat().st_mtime_ns, get_settings().public_base_url)
    return _send(request, body, "text/html; charset=utf-8", "no-cache, must-revalidate")
