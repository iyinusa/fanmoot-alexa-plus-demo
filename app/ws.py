"""``/ws`` — the one channel between a visitor's browser and this demo.

Conversation turns and MCP trace frames will both travel here (HAD-10): the
browser never holds a model credential, an MCP session or an OAuth token, so
everything it learns about a turn it learns over this socket.

**W0 is the transport, not the protocol.** It accepts, checks the origin,
greets, answers ``ping``, and refuses anything else with a typed error. The
turn protocol — partial text, tool-call start and end, view payloads (W1-5) —
and the trace frames (W1-6) are built on top of exactly this loop.

Every frame, both ways, is a JSON object with a ``type``. The shape is kept
that strict from the first frame so W1 extends it rather than migrates it.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app import __version__
from app.logging_config import get_logger
from app.settings import get_settings

router = APIRouter()
log = get_logger("ws")

#: The demo's own wire protocol, sent in `hello`. Bumped when a frame changes
#: shape, so a tab left open across a deploy can tell it needs a reload.
WIRE_VERSION = 1


def _error(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


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

    await websocket.accept()
    session_id = secrets.token_urlsafe(12)
    structlog.contextvars.bind_contextvars(ws_session=session_id)
    log.info("ws_open", origin=origin)

    try:
        await websocket.send_json(
            {
                "type": "hello",
                "session_id": session_id,
                "service": settings.service_name,
                "version": __version__,
                "wire": WIRE_VERSION,
                "mcp_url": settings.mcp_url,
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            if text is None:
                await websocket.send_json(_error("bad_frame", "Frames are JSON text."))
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
                await websocket.send_json(_error("bad_frame", "Frames are JSON text."))
                continue
            if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                await websocket.send_json(
                    _error("bad_frame", "A frame is an object with a string `type`.")
                )
                continue

            if frame["type"] == "ping":
                # `t` is echoed so the client can measure the round trip.
                await websocket.send_json({"type": "pong", "t": frame.get("t")})
            else:
                await websocket.send_json(
                    _error("unknown_type", f"Unknown frame type {frame['type']!r}.")
                )
    except WebSocketDisconnect:
        pass
    finally:
        log.info("ws_close")
        structlog.contextvars.unbind_contextvars("ws_session")
