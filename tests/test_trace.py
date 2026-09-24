"""The trace channel: redacted at the source, numbered, bounded (W1-6)."""

from __future__ import annotations

import json
from typing import Any

from app.agent.trace import RING_SIZE, Tracer, bounded, redact


def test_credentials_are_struck_out_wherever_they_are() -> None:
    payload = {
        "access_token": "at-123",
        "nested": {"refresh_token": "rt-456", "keep": "football"},
        "list": [{"code_verifier": "v"}, "Authorization: Bearer abc.def"],
        "note": "mail jo@example.com about it",
        "state": "one-time",
    }

    cleaned = json.dumps(redact(payload))

    for secret in ("at-123", "rt-456", "abc.def", "jo@example.com", "one-time"):
        assert secret not in cleaned
    assert "football" in cleaned


def test_a_challenge_is_not_mistaken_for_a_credential() -> None:
    challenge = 'Bearer error="insufficient_scope", scope="moot.write"'

    assert redact(challenge) == challenge
    assert redact("Bearer abc.def-123") == "Bearer [redacted]"


def test_a_large_payload_becomes_a_preview() -> None:
    body, size = bounded({"html": "x" * 50_000}, 24_000)

    assert size > 50_000
    assert body["truncated"] is True
    assert len(body["preview"]) == 2000


def test_frames_are_numbered_and_sent() -> None:
    sent: list[dict[str, Any]] = []
    tracer = Tracer(sink=sent.append)

    tracer.emit("mcp", direction="out", method="initialize", rpc_id=0, payload={"token": "t"})
    tracer.emit("mcp", direction="in", method="initialize", rpc_id=0, ms=12.345)

    assert [frame["seq"] for frame in sent] == [1, 2]
    assert sent[0]["payload"] == {"token": "[redacted]"}
    assert sent[1]["ms"] == 12.3


def test_a_broken_sink_does_not_break_the_caller() -> None:
    def explode(_frame: dict[str, Any]) -> None:
        raise RuntimeError("socket closed")

    tracer = Tracer(sink=explode)

    frame = tracer.emit("session", direction="local", method="connect")

    assert frame["seq"] == 1


def test_the_ring_keeps_the_most_recent_frames() -> None:
    tracer = Tracer()
    for _ in range(RING_SIZE + 5):
        tracer.emit("mcp", direction="out", method="ping")

    assert len(tracer.ring) == RING_SIZE
    assert tracer.ring[0]["seq"] == 6
