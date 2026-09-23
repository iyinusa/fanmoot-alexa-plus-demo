"""Serving the built SPA, and the client-side routing fallback.

The same shape as ``fanmoot``'s ``app/spa.py``, trimmed to one bundle. This
router is registered **last**, so ``/healthz``, ``/ws`` and ``/api/*`` get first
refusal and only what nothing else claimed falls through to the shell.

Unmatched paths under ``/api/`` and the asset directories return a real 404
rather than ``index.html``: an API call answered with HTML is a JSON parse
error that looks like a bug in the caller, and a mistyped font URL answered
with a 200 is a silent fallback face that nothing ever reports.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, Response

router = APIRouter(include_in_schema=False)

#: Where `vite build` writes (web/vite.config.ts) and the Docker image copies
#: the bundle to. Module-level so a test can point it at a directory it built.
WEB_DIR = Path(__file__).resolve().parent / "static" / "web"

#: Prefixes the SPA must never answer for.
_NEVER_SPA = ("/api/", "/ws", "/healthz", "/.well-known/")

#: Directories of real files in the bundle. Missing under one of these is a
#: wrong URL, never an app route.
_ASSET_DIRS = ("/assets/", "/fonts/", "/icons/")

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


# HEAD as well as GET: `@router.get` alone answers HEAD with a 405, and uptime
# monitors and link-preview scrapers ask with HEAD before anything else.
@router.api_route("/{path:path}", methods=["GET", "HEAD"])
async def web_spa(path: str) -> Response:
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
            return FileResponse(candidate, headers={"Cache-Control": cache})
        if any(full.startswith(prefix) for prefix in _ASSET_DIRS):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    index = WEB_DIR / "index.html"
    if not index.is_file():
        return _not_built()
    # The shell names the current hashed bundle, so it is never cached: a
    # cached one pins a judge's browser to a deploy that no longer exists.
    return FileResponse(index, headers={"Cache-Control": "no-cache, must-revalidate"})
