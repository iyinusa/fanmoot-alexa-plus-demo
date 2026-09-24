"""The browser's side of signing in (W1-7): three plain HTTP routes.

``GET /oauth/start?ticket=…``
    What the "Sign in to Fanmoot" popup opens. The ticket (from the ``session``
    frame) says which visitor this is without putting their resume token in a
    URL; it is single-use. The route starts a PKCE authorization on that
    visitor's session and redirects the popup to the real
    ``mcp.fanmoot.com/authorize``.
``GET /oauth/callback``
    Where the authorization server sends the fan back. The code is redeemed
    **here, server-side** — the browser never sees a token — and the grant is
    attached to the visitor's session, whose socket hears about it as an
    ``auth`` frame. The popup is then sent to ``/linked``, an SPA route that
    tells its opener and closes itself.
``GET /oauth/client-metadata.json``
    This demo's own OAuth client metadata. On an ``https`` deployment its URL
    *is* the demo's ``client_id`` (SEP-991), and the authorization server
    fetches it on first use.

Every failure ends at ``/linked?status=…`` with a short reason rather than a
stack trace or a JSON body: the fan is looking at a popup.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.agent.oauth import OAuthError
from app.logging_config import get_logger
from app.session import SessionRegistry

router = APIRouter(prefix="/oauth", include_in_schema=False)
log = get_logger("oauth.routes")

_NO_STORE = {"Cache-Control": "no-store"}


def _done(status: str, reason: str = "") -> RedirectResponse:
    query = {"status": status}
    if reason:
        query["reason"] = reason
    return RedirectResponse(f"/linked?{urlencode(query)}", status_code=303, headers=_NO_STORE)


def _registry(request: Request) -> SessionRegistry:
    registry: SessionRegistry = request.app.state.registry
    return registry


@router.get("/start")
async def start(request: Request, ticket: str = "") -> Response:
    session = _registry(request).by_ticket(ticket) if ticket else None
    if session is None:
        return _done("error", "This sign-in link has expired. Press Sign in again.")
    try:
        url = await session.begin_sign_in()
    except OAuthError as exc:
        log.warning("oauth_start_failed", error=exc.error)
        return _done("error", "Fanmoot's sign-in could not be reached just now.")
    return RedirectResponse(url, status_code=302, headers=_NO_STORE)


@router.get("/callback")
async def callback(
    request: Request,
    state: str = "",
    code: str = "",
    iss: str | None = None,
    error: str = "",
) -> Response:
    session = _registry(request).by_state(state) if state else None
    if session is None:
        return _done("error", "This sign-in is not one this demo started, or it has expired.")
    if error:
        session.pending.pop(state, None)
        if error == "access_denied":
            session.send_auth(reason="Sign-in was cancelled.")
            return _done("denied")
        log.warning("oauth_callback_error", error=error)
        return _done("error", "Fanmoot could not complete the sign-in.")
    if not code:
        return _done("error", "The sign-in came back without a code.")
    try:
        await session.complete_sign_in(state, code=code, iss=iss)
    except OAuthError as exc:
        log.warning("oauth_exchange_failed", error=exc.error)
        return _done("error", "The sign-in could not be completed. Try again.")
    return _done("linked", f"@{session.handle}" if session.handle else "")


@router.get("/client-metadata.json")
async def client_metadata(request: Request) -> JSONResponse:
    registry = _registry(request)
    return JSONResponse(
        registry.oauth.metadata_document(),
        headers={"Cache-Control": "public, max-age=300"},
    )
