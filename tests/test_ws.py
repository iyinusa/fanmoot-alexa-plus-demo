"""``/ws``: the origin check, the frame discipline, and the session protocol (W1-5)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.settings import Settings
from app.ws import WIRE_VERSION
from tests.fake_as import ISSUER, FakeAuthorizationServer
from tests.fake_mcp import RunningServer
from tests.scripted import ScriptedLlm, call, say
from tests.test_runtime import _Hanging


def until(socket: WebSocketTestSession, kind: str, limit: int = 200) -> dict[str, Any]:
    """The next frame of this type. Trace and facts frames arrive in between,
    on their own schedule, so a test waits for the one it is about."""
    for _ in range(limit):
        frame = socket.receive_json()
        if frame["type"] == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame")


def collect(socket: WebSocketTestSession, stop: str) -> list[dict[str, Any]]:
    frames = []
    while True:
        frame = socket.receive_json()
        frames.append(frame)
        if frame["type"] == stop:
            return frames


@pytest.fixture
def live_client(
    configure: Callable[..., Settings], mcp_server: RunningServer
) -> Iterator[TestClient]:
    """The app, pointed at the fake MCP server, with a model to script."""
    configure(mcp_url=mcp_server.url)
    with TestClient(create_app()) as test_client:
        yield test_client


def script(client: TestClient, llm: Any) -> None:
    client.app.state.registry.llm_factory = lambda _rung, _settings: llm  # type: ignore[attr-defined]


# ── W0's transport, unchanged ────────────────────────────────────────────────
def test_greets_with_hello(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()

    assert hello["type"] == "hello"
    assert hello["service"] == "fanmoot-alexa-plus-demo"
    assert hello["wire"] == WIRE_VERSION == 6
    assert hello["mcp_url"] == "https://mcp.fanmoot.test/mcp"
    assert len(hello["session_id"]) >= 16
    assert len(hello["resume_token"]) >= 32


def test_every_connection_is_its_own_session(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first, client.websocket_connect("/ws") as second:
        assert first.receive_json()["session_id"] != second.receive_json()["session_id"]


def test_ping_is_answered_with_its_timestamp(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "ping", "t": 1234})

        assert until(socket, "pong") == {"type": "pong", "t": 1234}


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("not json", "bad_frame"),
        ("[1, 2]", "bad_frame"),
        ('{"no": "type"}', "bad_frame"),
        ('{"type": 7}', "bad_frame"),
        ('{"type": "dance"}', "unknown_type"),
        ('{"type": "turn"}', "bad_turn"),
        ('{"type": "turn", "text": "   "}', "bad_turn"),
    ],
)
def test_a_bad_frame_is_answered_and_the_socket_survives(
    client: TestClient, raw: str, code: str
) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.send_text(raw)

        assert until(socket, "error")["code"] == code
        # Still open: one malformed frame is not a reason to drop a visitor.
        socket.send_json({"type": "ping"})
        assert until(socket, "pong")["type"] == "pong"


def test_audio_with_no_listening_open_goes_nowhere(client: TestClient) -> None:
    # Binary frames are the fan's voice (app.voice.listen). With no
    # `listen.start` before them there is nothing to relay them to, and the
    # socket carries on.
    with client.websocket_connect("/ws") as socket:
        socket.send_bytes(b"\x00\x01")
        socket.send_json({"type": "ping", "t": 1})

        assert until(socket, "pong")["t"] == 1


def test_an_oversized_audio_frame_closes_the_socket(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.send_bytes(b"\x00" * 100_000)

        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(100):
                socket.receive_json()
    assert closed.value.code == 1009


def test_an_oversized_frame_closes_the_socket(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps({"type": "ping", "pad": "x" * 20_000}))

        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(100):
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


# ── W1: the session protocol ─────────────────────────────────────────────────
def test_the_session_frame_follows_hello(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        hello = socket.receive_json()
        session = until(socket, "session")

    assert session["session_id"] == hello["session_id"]
    assert session["resumed"] is False
    assert session["auth"]["mode"] == "none"
    assert session["auth"]["method"] is None  # no grant, so no claim of OAuth
    assert session["sign_in_url"].startswith("/oauth/start?ticket=")
    assert session["turns_left"] == 40


def test_facts_are_read_from_the_live_session(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        facts = until(socket, "facts")

    assert facts["connected"] is True
    assert facts["mcp"]["protocol_version"] == "2025-11-25"
    assert facts["mcp"]["transport"] == "Streamable HTTP"
    assert facts["mcp"]["server_name"] == "fanmoot-fake"
    assert facts["models"]["primary"] == "scripted/primary"


def test_facts_carry_what_the_authorization_server_advertises(live_client: TestClient) -> None:
    oauth = live_client.app.state.registry.oauth  # type: ignore[attr-defined]
    oauth._transport = FakeAuthorizationServer().transport

    with live_client.websocket_connect("/ws") as socket:
        facts = until(socket, "facts")
        # The first facts go out as soon as the MCP session is up; discovery
        # follows, before anyone has signed in.
        while facts["oauth"] is None:
            facts = until(socket, "facts")

    assert facts["oauth"]["pkce"] == "S256"
    assert facts["oauth"]["issuer"] == ISSUER
    assert facts["auth"]["method"] is None  # advertised is not the same as held


def test_a_turn_over_the_socket(live_client: TestClient) -> None:
    script(live_client, ScriptedLlm.of(call("live_scores"), say("Two-one to Arsenal.")))

    with live_client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "turn", "text": "what's the score?"})
        frames = collect(socket, "turn.end")

    kinds = [frame["type"] for frame in frames]
    assert kinds.index("turn.start") < kinds.index("tool.start") < kinds.index("tool.end")
    assert kinds.index("view") < kinds.index("tool.end") < kinds.index("text.delta")
    end = frames[-1]
    assert end["text"] == "Two-one to Arsenal."
    assert end["model"] == "scripted/primary"


def test_every_page_hears_how_many_are_online(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        assert until(first, "presence")["online"] == 1

        with client.websocket_connect("/ws") as second:
            assert until(second, "presence")["online"] == 2
            assert until(first, "presence")["online"] == 2

        # The second page closed: its session is kept for a resume, but
        # nobody is looking at it.
        assert until(first, "presence")["online"] == 1


def test_a_resumed_page_is_told_the_count(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        hello = first.receive_json()

    with client.websocket_connect("/ws") as second:
        until(second, "presence")
        second.send_json(
            {"type": "resume", "session_id": hello["session_id"], "token": hello["resume_token"]}
        )
        # The fresh session gave way to the resumed one: still one page.
        assert until(second, "presence")["online"] == 1


def test_a_session_resumes_on_a_new_socket(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        hello = first.receive_json()

    with client.websocket_connect("/ws") as second:
        second.receive_json()
        second.send_json(
            {"type": "resume", "session_id": hello["session_id"], "token": hello["resume_token"]}
        )
        resumed = until(second, "session")
        while resumed["resumed"] is False:
            resumed = until(second, "session")

    assert resumed["session_id"] == hello["session_id"]


def test_a_wrong_resume_token_is_refused(client: TestClient) -> None:
    with client.websocket_connect("/ws") as first:
        hello = first.receive_json()

    with client.websocket_connect("/ws") as second:
        second.send_json({"type": "resume", "session_id": hello["session_id"], "token": "guess"})
        assert until(second, "error")["code"] == "resume_failed"


def test_one_turn_at_a_time_and_cancel(live_client: TestClient) -> None:
    script(live_client, _Hanging())

    with live_client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "turn", "text": "score?"})
        until(socket, "turn.start")
        socket.send_json({"type": "turn", "text": "and now?"})
        assert until(socket, "error")["code"] == "busy"
        socket.send_json({"type": "cancel"})
        assert until(socket, "turn.end")["status"] == "cancelled"


def test_reset_forgets_the_conversation(live_client: TestClient) -> None:
    script(live_client, ScriptedLlm.of(say("One.")))

    with live_client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "turn", "text": "one"})
        until(socket, "turn.end")
        socket.send_json({"type": "reset"})
        until(socket, "session.reset")


def test_there_is_no_shared_demo_account(client: TestClient) -> None:
    # Fans register and sign in as themselves: the old shared account is gone.
    with client.websocket_connect("/ws") as socket:
        session = until(socket, "session")
        socket.send_json({"type": "auth.demo"})
        error = until(socket, "error")

    assert "demo_fan" not in session
    assert error["code"] == "unknown_type"
