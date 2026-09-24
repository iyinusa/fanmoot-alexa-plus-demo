"""The MCP session: real Streamable HTTP, traced, and robust to a 401 (W1-1, W1-6)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.agent.mcp_client import AuthChallenge, McpConnection, McpUnavailable
from app.agent.trace import Tracer
from tests import fake_mcp
from tests.fake_mcp import RunningServer


def _connection(
    url: str, *, token: str | None = None
) -> tuple[McpConnection, list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []

    async def provider() -> str | None:
        return token

    return (
        McpConnection(url, tracer=Tracer(sink=frames.append), token_provider=provider, timeout_s=5),
        frames,
    )


def _mcp(frames: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(frame["dir"], frame["method"]) for frame in frames if frame["kind"] == "mcp"]


async def test_negotiates_2025_11_25_and_lists_the_servers_tools(mcp_server: RunningServer) -> None:
    connection, frames = _connection(mcp_server.url)
    try:
        await connection.connect()

        facts = connection.facts
        assert facts is not None
        # The number the track turns on, read from the live handshake.
        assert facts.protocol_version == "2025-11-25"
        assert facts.transport == "Streamable HTTP"
        assert facts.server_name == "fanmoot-fake"
        assert facts.instructions == fake_mcp.INSTRUCTIONS
        assert facts.session_id
        assert {tool.name for tool in connection.tools} >= {"live_scores", "post_moot"}
    finally:
        await connection.close()

    assert _mcp(frames)[:5] == [
        ("out", "initialize"),
        ("in", "initialize"),
        ("out", "notifications/initialized"),
        ("out", "tools/list"),
        ("in", "tools/list"),
    ]
    # Responses carry their round trip.
    assert all(f["ms"] is not None for f in frames if f["kind"] == "mcp" and f["dir"] == "in")


async def test_a_tool_call_and_its_view_are_on_the_trace(mcp_server: RunningServer) -> None:
    connection, frames = _connection(mcp_server.url)
    try:
        result = await connection.call_tool("live_scores", {"limit": 3})
        view = await connection.read_resource("ui://fanmoot/scoreboard")
    finally:
        await connection.close()

    assert result.content[0].text.startswith("Arsenal are two-one up")  # type: ignore[union-attr]
    assert view.contents[0].mimeType == "text/html;profile=mcp-app"
    calls = [f for f in frames if f["method"] == "tools/call"]
    assert [f["dir"] for f in calls] == ["out", "in"]
    assert calls[0]["tool"] == "live_scores"
    assert calls[1]["summary"].startswith("Arsenal are two-one up")
    # The session is ended politely, and that is on the trace too.
    assert any(f["method"] == "DELETE /mcp" for f in frames)


async def test_a_401_is_a_challenge_and_the_session_survives(mcp_server: RunningServer) -> None:
    connection, frames = _connection(mcp_server.url)
    try:
        await connection.connect()
        started = time.perf_counter()
        with pytest.raises(AuthChallenge) as refused:
            await connection.call_tool("post_moot", {"body": "Saka was offside"})
        # Answered at once, not after the request timeout.
        assert time.perf_counter() - started < 2

        assert refused.value.status == 401
        assert refused.value.resource_metadata is not None
        assert refused.value.resource_metadata.endswith("/.well-known/oauth-protected-resource/mcp")

        # mcp 1.30 would have torn the whole session down here. It is still up.
        generation = connection.generation
        again = await connection.call_tool("live_scores", {})
        assert again.isError is False
        assert connection.generation == generation
    finally:
        await connection.close()

    refusal = next(f for f in frames if f["method"] == "tools/call" and f["status"] == "error")
    assert refusal["http_status"] == 401
    assert "resource_metadata" in refusal["www_authenticate"]


async def test_a_grant_without_the_scope_is_a_403_naming_it(mcp_server: RunningServer) -> None:
    connection, _frames = _connection(mcp_server.url, token=fake_mcp.READER_TOKEN)
    try:
        with pytest.raises(AuthChallenge) as refused:
            await connection.call_tool("post_moot", {"body": "take"})
    finally:
        await connection.close()

    assert refused.value.status == 403
    assert refused.value.scope == "moot.write"
    assert refused.value.error == "insufficient_scope"


async def test_the_bearer_token_is_read_per_request(mcp_server: RunningServer) -> None:
    connection, _frames = _connection(mcp_server.url, token=fake_mcp.GOOD_TOKEN)
    try:
        result = await connection.call_tool("whoami", {})
    finally:
        await connection.close()

    assert result.structuredContent == {"handle": "demo_fan"}
    sent = [r for r in mcp_server.gate.requests if r["frame"].get("method") == "tools/call"]
    assert sent[-1]["headers"]["authorization"] == f"Bearer {fake_mcp.GOOD_TOKEN}"
    # Every request carries an id the server's log can be matched on.
    assert sent[-1]["headers"]["x-request-id"]


async def test_reconnect_is_a_fresh_initialize(mcp_server: RunningServer) -> None:
    connection, frames = _connection(mcp_server.url)
    try:
        await connection.connect()
        first = connection.facts.session_id if connection.facts else None
        await connection.reconnect("signed in to Fanmoot")
        second = connection.facts.session_id if connection.facts else None
    finally:
        await connection.close()

    assert first and second and first != second
    assert connection.generation == 2
    assert [m for d, m in _mcp(frames) if d == "out" and m == "initialize"] == ["initialize"] * 2
    assert any("signed in to Fanmoot" in f["summary"] for f in frames if f["kind"] == "session")


async def test_an_unreachable_server_fails_fast_and_says_so() -> None:
    connection, frames = _connection("http://127.0.0.1:1/mcp")

    started = time.perf_counter()
    with pytest.raises(McpUnavailable):
        await connection.connect()

    assert time.perf_counter() - started < 5
    assert connection.connected is False
    assert any(f["status"] == "error" for f in frames)
