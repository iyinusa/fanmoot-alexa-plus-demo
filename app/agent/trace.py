"""The trace channel: every MCP frame, OAuth request and model call, as it happens.

HAD-12's point is that the judges cannot see inside a voice interaction, and
this is what lets them. Four kinds of frame reach the trace panel (W3):

``mcp``
    One JSON-RPC message, in or out — ``initialize``, ``tools/list``,
    ``tools/call``, ``resources/read`` and the notifications between them. A
    response carries the round trip in ``ms``, measured from the moment its
    request left this process.
``oauth``
    One HTTP exchange with the authorization server: discovery, registration,
    the token exchange, a refresh, a revocation. OAuth is not JSON-RPC, but it
    is the half of the integration a judge is most likely to doubt.
``model``
    One model call: which model answered, how long it took, the tokens and the
    estimated spend, and any failover on the way (W1-3b, W1-10).
``session``
    What the demo itself did to the MCP session: connecting, reconnecting, a
    request the server refused with a 401.

**Redaction happens here, at the source**, not in the browser. Anything named
like a credential, any ``Bearer …`` text and any email address is struck out
before a frame is queued, so no token or address ever crosses ``/ws`` — the
trace panel cannot leak what it was never sent. Frames are logged as well
(without the payload), so this demo's log lines up with ``fanmoot-mcp``'s.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from app.logging_config import REDACTED, SENSITIVE_KEYS, get_logger

log = get_logger("trace")

#: Frames kept per session, for a browser that reconnects mid-turn.
RING_SIZE = 400

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
#: A bearer credential — but not a ``WWW-Authenticate`` challenge, whose
#: ``Bearer resource_metadata="…"`` carries no secret and is exactly what the
#: trace panel should show a judge.
_BEARER = re.compile(r'(?i)\b(bearer)\s+(?![A-Za-z_]+=")[A-Za-z0-9._~+/=-]+')

#: More keys than the log redactor strikes. ``state`` and ``code_challenge``
#: are harmless on their own, but they are single-use values from a live
#: authorization, and the panel explains the flow perfectly well without them.
_TRACE_SENSITIVE = SENSITIVE_KEYS | {
    "code_challenge",
    "email",
    "state",
    "api_key",
    "aws_secret_access_key",
    "aws_session_token",
}

FrameSink = Callable[[dict[str, Any]], None]


def redact(value: Any, key: str = "") -> Any:
    """A copy of ``value`` with credentials and email addresses struck out."""
    if key.lower() in _TRACE_SENSITIVE and value not in (None, ""):
        return REDACTED
    if isinstance(value, str):
        return _EMAIL.sub(REDACTED, _BEARER.sub(r"\1 " + REDACTED, value))
    if isinstance(value, Mapping):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def bounded(payload: Any, limit: int) -> tuple[Any, int]:
    """The payload as the panel should receive it, and its size in bytes.

    Past ``limit`` the panel gets a preview rather than the whole thing: a
    ``ui://`` view is tens of kilobytes of HTML, and the panel wants to show
    that it arrived, not to render it twice.
    """
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    size = len(text.encode())
    if size <= limit:
        return payload, size
    return {"truncated": True, "bytes": size, "preview": text[:2000]}, size


class Tracer:
    """One visitor's trace: numbered, redacted, timed, and kept in a ring.

    ``sink`` is where a frame goes once it is built — the visitor's socket, by
    way of its outbox. It is replaceable because a session outlives any one
    socket (a phone reconnecting), and the frames are the session's.
    """

    def __init__(self, *, max_payload_bytes: int = 24_000, sink: FrameSink | None = None) -> None:
        self.max_payload_bytes = max_payload_bytes
        self.sink = sink
        self._seq = 0
        self.ring: deque[dict[str, Any]] = deque(maxlen=RING_SIZE)

    def emit(
        self,
        kind: str,
        *,
        direction: str,
        method: str,
        summary: str = "",
        status: str = "ok",
        payload: Any = None,
        ms: float | None = None,
        rpc_id: str | int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build, redact, record and send one frame. Returns the frame."""
        self._seq += 1
        body, size = bounded(redact(payload), self.max_payload_bytes)
        frame: dict[str, Any] = {
            "seq": self._seq,
            "at": round(time.time() * 1000),
            "kind": kind,
            "dir": direction,
            "method": method,
            "id": rpc_id,
            "status": status,
            "ms": None if ms is None else round(ms, 1),
            "summary": redact(summary),
            "bytes": size,
            "payload": body,
        }
        frame.update({key: redact(value, key) for key, value in extra.items()})
        self.ring.append(frame)

        # The log line carries everything but the payload: enough to line a
        # frame up with the server's own log by method, id and time, without
        # writing a fan's words into this service's logs.
        log.info(
            "trace_frame",
            kind=kind,
            dir=direction,
            method=method,
            rpc_id=rpc_id,
            status=status,
            ms=frame["ms"],
            bytes=size,
        )
        if self.sink is not None:
            try:
                self.sink(frame)
            except Exception:  # a closed socket must not break the MCP session
                log.debug("trace_sink_failed", seq=self._seq)
        return frame
