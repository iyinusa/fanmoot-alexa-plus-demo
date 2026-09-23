"""``/ws``: the origin check, the greeting, and the frame discipline."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.ws import WIRE_VERSION


def test_greets_with_hello(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()

    assert hello["type"] == "hello"
    assert hello["service"] == "fanmoot-alexa-plus-demo"
    assert hello["wire"] == WIRE_VERSION
    assert hello["mcp_url"] == "https://mcp.fanmoot.test/mcp"
    assert len(hello["session_id"]) >= 16


def test_every_connection_is_its_own_session(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        assert first.receive_json()["session_id"] != second.receive_json()["session_id"]


def test_ping_is_answered_with_its_timestamp(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_json({"type": "ping", "t": 1234})

        assert socket.receive_json() == {"type": "pong", "t": 1234}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not json", "bad_frame"),
        ("[1, 2]", "bad_frame"),
        ('{"no": "type"}', "bad_frame"),
        ('{"type": 7}', "bad_frame"),
        ('{"type": "turn"}', "unknown_type"),
    ],
)
def test_a_bad_frame_is_answered_and_the_socket_survives(
    client: TestClient, raw: str, code: str
) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_text(raw)

        assert socket.receive_json()["code"] == code
        # Still open: one malformed frame is not a reason to drop a visitor.
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"


def test_binary_frames_are_refused(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_bytes(b"\x00\x01")

        assert socket.receive_json()["code"] == "bad_frame"


def test_an_oversized_frame_closes_the_socket(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_text(json.dumps({"type": "ping", "pad": "x" * 20_000}))

        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
    assert closed.value.code == 1009


def test_a_foreign_origin_is_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/ws", headers={"Origin": "https://evil.example"}),
    ):
        pass
    assert refused.value.code == 1008


def test_the_demos_own_origin_is_accepted(client: TestClient) -> None:
    with client.websocket_connect("/ws", headers={"Origin": "http://testserver"}) as socket:
        assert socket.receive_json()["type"] == "hello"
