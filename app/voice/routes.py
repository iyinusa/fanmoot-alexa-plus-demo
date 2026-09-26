"""``POST /api/speech`` — one sentence of Alexa's, as MP3 from Polly.

Plain HTTP rather than a frame on ``/ws``: the browser plays audio it fetched
far more simply than audio reassembled from a socket, and it can fetch the
next sentence while this one is still being said.

**Only a live session may ask**, identified the way a resumed socket is — its
id and resume token, sent as headers (never in the URL, where a log would keep
them). The session's allowance and the day's ceilings are checked before Polly
is. The request's ``Origin``, when there is one, must be one ``/ws`` accepts,
so another site cannot spend this demo's voice through a visitor's browser.

Refusals are JSON with a ``code``, and every one of them means the same thing
to the browser: say it with your own voice instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.logging_config import get_logger
from app.session import SessionRegistry
from app.settings import get_settings
from app.voice.aws import VoiceUnavailable
from app.voice.speech import MAX_CHARS

router = APIRouter(prefix="/api", include_in_schema=False)
log = get_logger("voice.routes")

_NO_STORE = {"Cache-Control": "no-store"}


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)


def _refused(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status, headers=_NO_STORE)


@router.post("/speech")
async def speech(
    body: SpeechRequest,
    request: Request,
    x_fanmoot_session: str = Header(default=""),
    x_fanmoot_token: str = Header(default=""),
) -> Response:
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().ws_origins:
        return _refused(403, "origin", "Not from this demo.")

    registry: SessionRegistry = request.app.state.registry
    session = registry.resume(x_fanmoot_session, x_fanmoot_token)
    if session is None:
        return _refused(401, "no_session", "No live session.")

    try:
        audio = await session.speak(body.text)
    except VoiceUnavailable as exc:
        status = 429 if exc.code in {"voice_cap", "daily_cap"} else 503
        if exc.code in {"empty", "too_long"}:
            status = 400
        return _refused(status, exc.code, exc.message)

    voice = f"Amazon Polly; {session.settings.voice_id}; {session.speaker.engine}"
    return Response(audio, media_type="audio/mpeg", headers={**_NO_STORE, "X-Voice": voice})
