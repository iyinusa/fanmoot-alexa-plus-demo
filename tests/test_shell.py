"""W2's server side: the sandbox the views render in, a card's own calls, and the ticker."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.main import create_app
from app.settings import Settings
from tests.fake_mcp import RunningServer
from tests.test_ws import until


def _call(
    socket: WebSocketTestSession, call_id: int, name: str, **arguments: Any
) -> dict[str, Any]:
    socket.send_json({"type": "view.call", "id": call_id, "name": name, "arguments": arguments})
    result = until(socket, "view.call.result")
    assert result["id"] == call_id
    return result


def _traced(frames: list[dict[str, Any]], origin: str) -> list[dict[str, Any]]:
    return [
        frame["frame"]
        for frame in frames
        if frame.get("type") == "trace" and frame["frame"].get("origin") == origin
    ]


def _drain(socket: WebSocketTestSession, stop: str) -> list[dict[str, Any]]:
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
    configure(mcp_url=mcp_server.url)
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def ticking_client(
    configure: Callable[..., Settings], mcp_server: RunningServer
) -> Iterator[TestClient]:
    configure(mcp_url=mcp_server.url, ambient_interval_s=30)
    with TestClient(create_app()) as test_client:
        yield test_client


# ── /sandbox (W2-6) ──────────────────────────────────────────────────────────
def test_the_sandbox_may_be_framed_by_the_shell_and_nothing_else(client: TestClient) -> None:
    response = client.get("/sandbox")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp
    assert "default-src 'none'" in csp
    assert "form-action 'none'" in csp
    # Nothing a view runs may reach the visitor's microphone.
    assert "microphone=()" in response.headers["permissions-policy"]


def test_the_sandbox_speaks_the_specs_handshake(client: TestClient) -> None:
    body = client.get("/sandbox").text

    assert "ui/notifications/sandbox-' + 'proxy-ready" not in body  # built from a prefix…
    assert "var PREFIX = 'ui/notifications/sandbox-';" in body
    assert "PREFIX + 'proxy-ready'" in body and "PREFIX + 'resource-ready'" in body
    # The spec's inner sandbox, plus forms for the reply composer.
    assert "'allow-scripts allow-same-origin allow-forms'" in body
    # The view's own policy is built from its declared domains, not a fixed one.
    assert "csp.resourceDomains" in body and "csp.connectDomains" in body


def test_the_sandbox_answers_head(client: TestClient) -> None:
    assert client.head("/sandbox").status_code == 200


def test_the_shell_frames_only_its_own_origin(client: TestClient, web_dir: Any) -> None:
    csp = client.get("/").headers["content-security-policy"]
    assert "frame-src 'self'" in csp


# ── A card's calls (W2-6) ────────────────────────────────────────────────────
def test_a_card_may_call_a_tool_marked_for_apps(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        until(socket, "facts")
        socket.send_json(
            {"type": "view.call", "id": 1, "name": "app_only_refresh", "arguments": {}}
        )
        frames = _drain(socket, "view.call.result")

    result = frames[-1]
    assert result["id"] == 1
    assert result["result"]["content"][0]["text"] == "ok"
    # On the trace like any call, and marked as the card's rather than the agent's.
    tagged = _traced(frames, "view")
    assert [(frame["dir"], frame["tool"]) for frame in tagged] == [
        ("out", "app_only_refresh"),
        ("in", "app_only_refresh"),
    ]


def test_a_card_may_not_call_a_tool_kept_for_the_model(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        until(socket, "facts")
        result = _call(socket, 2, "model_only_hint")

    assert result["error"]["code"] == -32602
    assert "not callable from a card" in result["error"]["message"]


def test_a_card_calling_a_tool_that_does_not_exist_is_told_so(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        until(socket, "facts")
        result = _call(socket, 3, "delete_everything")

    assert result["error"]["code"] == -32602


def test_a_write_from_a_card_without_a_grant_asks_for_one(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        until(socket, "facts")
        socket.send_json(
            {"type": "view.call", "id": 4, "name": "post_moot", "arguments": {"body": "Offside."}}
        )
        frames = _drain(socket, "view.call.result")

    kinds = [frame["type"] for frame in frames]
    required = frames[kinds.index("auth.required")]
    assert required["tool"] == "post_moot" and required["status"] == 401
    # A refusal in the tool's own shape, so the card shows it beside the fan's words.
    result = frames[-1]["result"]
    assert result["isError"] is True
    assert "Sign in to Fanmoot" in result["content"][0]["text"]


def test_a_card_call_needs_an_id_and_a_name(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "view.call", "name": "app_only_refresh", "arguments": {}})
        assert until(socket, "error")["code"] == "bad_view_call"
        socket.send_json({"type": "view.call", "id": 5, "arguments": {}})
        assert until(socket, "view.call.result")["error"]["code"] == -32602


def test_a_card_has_a_budget(configure: Callable[..., Settings], mcp_server: RunningServer) -> None:
    configure(mcp_url=mcp_server.url, view_calls_per_session=1)
    with TestClient(create_app()) as client, client.websocket_connect("/ws") as socket:
        until(socket, "facts")
        assert "result" in _call(socket, 1, "app_only_refresh")
        assert "all the calls" in _call(socket, 2, "app_only_refresh")["error"]["message"]


# ── The ticker (W2-7) ────────────────────────────────────────────────────────
def test_the_ticker_shows_what_is_in_play(ticking_client: TestClient) -> None:
    with ticking_client.websocket_connect("/ws") as socket:
        frames = _drain(socket, "ambient")

    ambient = frames[-1]
    assert ambient["live"] is True
    assert [match["id"] for match in ambient["matches"]] == ["m1"]
    assert ambient["spoken"].startswith("Arsenal are two-one up")
    # Real calls on the visitor's own session, marked as the ticker's.
    assert {frame["tool"] for frame in _traced(frames, "ambient")} == {"live_scores"}


def test_the_ticker_is_off_when_its_interval_is_zero(live_client: TestClient) -> None:
    with live_client.websocket_connect("/ws") as socket:
        frames = [socket.receive_json() for _ in range(8)]
        socket.send_json({"type": "ping", "t": 1})
        frames += _drain(socket, "pong")

    assert "ambient" not in {frame["type"] for frame in frames}


def test_an_unchanged_ticker_is_not_sent_again(client: TestClient) -> None:
    session = client.app.state.registry.create()  # type: ignore[attr-defined]
    before = len(session.outbox._buffer)
    matches = [{"id": "m1", "status": "live"}]

    session._send_ambient(live=True, matches=matches, spoken="Two-one.")
    session._send_ambient(live=True, matches=matches, spoken="Two-one.")
    session._send_ambient(live=True, matches=[{"id": "m1", "status": "ht"}], spoken="Half-time.")

    sent = [frame for frame in list(session.outbox._buffer)[before:] if frame["type"] == "ambient"]
    assert [frame["spoken"] for frame in sent] == ["Two-one.", "Half-time."]
