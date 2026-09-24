"""ASGI middleware: request correlation, and the security headers every
response carries.

**Raw ASGI rather than ``BaseHTTPMiddleware``**, for the reason
``fanmoot-mcp`` records at length: ``BaseHTTPMiddleware`` runs the wrapped app
in a separate task and pipes the response through a memory stream, which is
fine for a JSON handler and not for a long-lived stream. Wrapping ``send``
adds the header and the log line with nothing between the handler and the
socket. WebSocket scopes pass straight through both; ``/ws`` logs its own
connect and disconnect.
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import get_logger

log = get_logger("http")

#: Not logged as a request line. Docker's HEALTHCHECK curls this every 30
#: seconds and Traefik probes it too; at that rate the health check would be
#: most of the log.
_QUIET_PATHS = frozenset({"/healthz"})


class RequestIdMiddleware:
    """Attach a request id to every request, response and log line.

    An inbound ``X-Request-ID`` is honoured so a trace can be followed across
    Traefik; otherwise one is minted. Bound into structlog's context-local
    storage on the same task as the handler, so a line written deep inside a
    handler carries the id of the request that caused it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 0

        async def tagged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, tagged)
        except Exception:
            log.exception(
                "request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                duration_ms=_since(started),
            )
            raise
        else:
            if scope.get("path") not in _QUIET_PATHS:
                log.info(
                    "request",
                    method=scope.get("method"),
                    path=scope.get("path"),
                    status=status,
                    duration_ms=_since(started),
                )
        finally:
            structlog.contextvars.unbind_contextvars("request_id")


def _since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


# ── Security headers ─────────────────────────────────────────────────────────
_ALWAYS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)

#: On HTML only, which in practice means the SPA shell.
#:
#: * ``script-src 'self'`` with no exceptions: the theme bootstrap is a file
#:   (``/theme.js``) rather than inline for exactly this reason, as in
#:   ``fanmoot``'s M15-5.
#: * ``img-src`` names ``fanmoot.com``, because club crests are fetched from it
#:   at runtime rather than copied here (HAD-11).
#: * ``connect-src 'self'`` covers ``/ws`` on this origin. The browser never
#:   talks to the MCP server directly — the session is held server-side (HAD-10).
#: * ``frame-src 'self'`` is for ``/sandbox`` alone (W2-6): the server's
#:   ``ui://`` views render inside it, framed with ``sandbox`` and no
#:   ``allow-same-origin``, so they run on an opaque origin of their own and
#:   never with this page's. ``app.sandbox`` sets that page's own policy.
#: * ``microphone=(self)``: the demo listens (W2-4), and nothing it embeds may.
_HTML_CSP = (
    b"default-src 'self'; script-src 'self'; style-src 'self'; "
    b"img-src 'self' data: https://fanmoot.com; font-src 'self'; "
    b"connect-src 'self'; frame-src 'self'; object-src 'none'; "
    b"base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
_HTML_ONLY = (
    (b"content-security-policy", _HTML_CSP),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin-allow-popups"),
    (b"permissions-policy", b"camera=(), microphone=(self), geolocation=(), payment=()"),
)

_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    """Add the headers a public HTTPS origin owes its visitors.

    A header a handler already set wins. HSTS goes out only when the configured
    public URL is ``https``: Traefik terminates TLS, so the scheme of this hop
    is always plain HTTP and says nothing about what the visitor typed.

    ``same-origin-allow-popups`` rather than ``same-origin``: W1-7 opens the
    real ``mcp.fanmoot.com/authorize`` in a popup, and a stricter policy would
    sever the handle this page needs to learn the popup has finished.
    """

    def __init__(self, app: ASGIApp, *, https: bool) -> None:
        self.app = app
        self.https = https

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                extra = list(_ALWAYS)
                if headers.get("content-type", "").startswith("text/html"):
                    extra.extend(_HTML_ONLY)
                if self.https:
                    extra.append(_HSTS)
                for name, value in extra:
                    key = name.decode("latin-1")
                    if key not in headers:
                        headers[key] = value.decode("latin-1")
            await send(message)

        await self.app(scope, receive, secured)
