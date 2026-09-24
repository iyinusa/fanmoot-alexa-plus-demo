"""``/ws`` — the one channel between a visitor's browser and this demo.

Conversation turns and MCP trace frames both travel here (HAD-10): the browser
never holds a model credential, an MCP session or an OAuth token, so everything
it learns about a turn it learns over this socket.

Every frame, both ways, is a JSON object with a ``type``.

**From the browser**

``ping {t}``                     answered with ``pong {t}``, for the round trip
``resume {session_id, token}``   pick up a session this browser had before
``turn {text}``                  ask something
``cancel``                       stop the turn in flight
``reset``                        forget the conversation (keep the sign-in)
``auth.demo``                    act as the shared demo fan (W1-8)
``auth.sign_out``                forget the fan's grant, and revoke it
``view.call {id, name, arguments}``  a card's ``tools/call`` through the host
                                 bridge (W2-6), answered with ``view.call.result``

Signing *in* is not a frame: the browser opens ``sign_in_url`` in a popup
(``app.oauth_routes``), because a popup has to open inside the click that
asked for it.

**From the server**

``hello``      the build and wire version, and the session's id and resume token
``session``    the session's state — identity, sign-in URL, turns left
``facts``      what the live MCP session reports about itself (W3-4)
``auth``       the fan's identity changed
``auth.required``  the server asked for a grant mid-turn
``turn.start`` · ``text.delta`` · ``tool.start`` · ``tool.end`` · ``view.resource``
``view`` · ``turn.end``  — one turn, as it happens (``app.agent.runtime``)
``trace``      one MCP, OAuth or model frame (``app.agent.trace``)
``view.call.result``  a card's call answered: ``result`` or ``error``, by ``id``
``ambient``    the corner ticker: what is in play, or what is next (W2-7)
``error``      something the visitor should know, with a ``code``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app import __version__
from app.agent.demo_fan import DemoFanUnavailable
from app.agent.oauth import OAuthError
from app.logging_config import get_logger
from app.session import SessionRegistry, VisitorSession
from app.settings import get_settings

router = APIRouter()
log = get_logger("ws")

#: The demo's own wire protocol, sent in `hello`. Bumped when a frame changes
#: shape, so a tab left open across a deploy can tell it needs a reload.
#: 2: W1's session and turn protocol.
#: 3: W2 — ``view.call`` / ``view.call.result`` and ``ambient``.
WIRE_VERSION = 3

#: The longest thing a fan can ask. A sentence said aloud, with room to spare.
MAX_TURN_CHARS = 1_000


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


class _Link:
    """The socket's current session, and the task writing its frames out."""

    def __init__(self, websocket: WebSocket, session: VisitorSession) -> None:
        self.websocket = websocket
        self.session = session
        self.writer: asyncio.Task[None] | None = None
        self.queue: asyncio.Queue[dict[str, Any]] | None = None

    def bind(self, session: VisitorSession) -> None:
        if self.writer is not None:
            self.writer.cancel()
        self.session = session
        queue = self.queue = session.attach()
        self.writer = asyncio.create_task(self._write(queue), name=f"ws-writer-{session.id}")

    async def _write(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            frame = await queue.get()
            try:
                await self.websocket.send_json(frame)
            except Exception:
                return

    async def unbind(self) -> None:
        self.session.detach(self.queue)
        if self.writer is not None:
            self.writer.cancel()
            with contextlib.suppress(BaseException):
                await self.writer


@router.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    settings = get_settings()

    # A WebSocket is not covered by CORS: without this check any page on the
    # internet could open a session here through a visitor's browser. Closing
    # before `accept` makes the handshake fail with a 403. A client that sends
    # no Origin at all is not a browser, and is not what this defends against.
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.ws_origins:
        log.warning("ws_origin_refused", origin=origin)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    registry: SessionRegistry = websocket.app.state.registry
    await websocket.accept()
    session = registry.create()
    if session is None:
        await websocket.send_json(
            _error("capacity", "The demo is full right now. Try again in a minute.")
        )
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    link = _Link(websocket, session)
    link.bind(session)
    structlog.contextvars.bind_contextvars(ws_session=session.id)
    log.info("ws_open", origin=origin)
    session.send(
        {
            "type": "hello",
            "session_id": session.id,
            "resume_token": session.resume_token,
            "service": settings.service_name,
            "version": __version__,
            "wire": WIRE_VERSION,
            "mcp_url": settings.mcp_url,
        }
    )
    session.send(session.state_frame())
    session.start()

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if link.session.closed:
                link.session.send(
                    _error("session_expired", "This session has ended. Reload to start again.")
                )
                await asyncio.sleep(0.05)
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                break

            text = message.get("text")
            if text is None:
                link.session.send(_error("bad_frame", "Frames are JSON text."))
                continue
            if len(text.encode()) > settings.ws_max_frame_bytes:
                # Closed rather than answered: a client sending frames this
                # size is not a visitor talking, and the next one would be too.
                log.warning("ws_frame_too_large", size=len(text))
                await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                break

            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                link.session.send(_error("bad_frame", "Frames are JSON text."))
                continue
            if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                link.session.send(_error("bad_frame", "A frame is an object with a string `type`."))
                continue

            await _handle(frame, link, registry)
    except WebSocketDisconnect:
        pass
    finally:
        await link.unbind()
        log.info("ws_close")
        structlog.contextvars.unbind_contextvars("ws_session")


async def _handle(frame: dict[str, Any], link: _Link, registry: SessionRegistry) -> None:
    session = link.session
    kind = frame["type"]

    if kind == "ping":
        # `t` is echoed so the client can measure the round trip.
        session.send({"type": "pong", "t": frame.get("t")})

    elif kind == "resume":
        target = registry.resume(str(frame.get("session_id") or ""), str(frame.get("token") or ""))
        if target is None:
            session.send(_error("resume_failed", "That session has ended; this is a new one."))
            return
        if target is session:
            return
        # The fresh session this socket opened with is not needed: drop it,
        # and its just-opened MCP session with it.
        link.bind(target)
        await registry.discard(session)
        structlog.contextvars.bind_contextvars(ws_session=target.id)
        log.info("ws_resumed")
        target.send(target.state_frame(resumed=True))
        target.send_facts()

    elif kind == "turn":
        text = frame.get("text")
        if not isinstance(text, str) or not text.strip():
            session.send(_error("bad_turn", "A turn needs some `text`."))
            return
        if len(text) > MAX_TURN_CHARS:
            session.send(_error("bad_turn", "That is longer than anyone says in one breath."))
            return
        refusal = session.start_turn(text.strip())
        if refusal == "busy":
            session.send(_error("busy", "Still answering the last one."))
        elif refusal == "turn_cap":
            session.send(
                _error("turn_cap", "This session has used all its turns. Reload to start again.")
            )

    elif kind == "cancel":
        await session.cancel_turn()

    elif kind == "reset":
        await session.reset()
        session.send(session.state_frame())

    elif kind == "auth.demo":
        if session.busy:
            session.send(_error("busy", "Wait for the answer, then switch accounts."))
            return
        try:
            await session.use_demo()
        except (DemoFanUnavailable, OAuthError) as exc:
            log.warning("demo_fan_unavailable", error=str(exc)[:200])
            session.send(
                _error(
                    "demo_unavailable",
                    str(exc)
                    if isinstance(exc, DemoFanUnavailable)
                    else "The demo account could not be linked just now.",
                )
            )

    elif kind == "view.call":
        call_id, name, arguments = frame.get("id"), frame.get("name"), frame.get("arguments", {})
        if not isinstance(call_id, str | int) or isinstance(call_id, bool):
            session.send(_error("bad_view_call", "A view call needs an `id`."))
            return
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            session.send(
                {
                    "type": "view.call.result",
                    "id": call_id,
                    "error": {
                        "code": -32602,
                        "message": "A call needs a tool `name` and `arguments`.",
                    },
                }
            )
            return
        session.start_view_call(call_id, name, arguments)

    elif kind == "auth.sign_out":
        if session.busy:
            session.send(_error("busy", "Wait for the answer, then sign out."))
            return
        await session.sign_out()

    else:
        session.send(_error("unknown_type", f"Unknown frame type {kind!r}."))
