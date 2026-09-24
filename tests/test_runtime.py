"""One turn, end to end: ADK over a real MCP session, as frames (W1-2, W1-4, W1-5).

The agent, the failover chain, the MCP client and the server are all real;
only the provider is scripted. So the assertions are about what the demo does
— which tools it offered, what it sent the visitor, in what order — against a
server speaking Streamable HTTP on a loopback port.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from app.agent.demo_fan import DemoFan
from app.agent.oauth import DemoOAuthClient, Grant, GrantHolder, RegisteredClient
from app.session import VisitorSession
from app.settings import Settings
from tests import fake_mcp
from tests.fake_mcp import RunningServer
from tests.scripted import ScriptedLlm, call, say


def _session(server: RunningServer, llm: BaseLlm, **overrides: Any) -> VisitorSession:
    settings = Settings(**{"mcp_url": server.url, "model_chain": "scripted/primary", **overrides})
    oauth = DemoOAuthClient(settings)
    return VisitorSession(
        settings,
        oauth=oauth,
        demo_fan=DemoFan(settings, oauth),
        llm_factory=lambda _rung, _settings: llm,
    )


async def _turn(session: VisitorSession, text: str) -> list[dict[str, Any]]:
    queue = session.outbox.attach() if not session.outbox.attached else None
    assert session.start_turn(text) is None
    assert session.turn_task is not None
    await session.turn_task
    frames: list[dict[str, Any]] = []
    source = queue or session.outbox._queue  # noqa: SLF001
    assert source is not None
    while not source.empty():
        frames.append(source.get_nowait())
    return frames


def _types(frames: list[dict[str, Any]]) -> list[str]:
    return [frame["type"] for frame in frames if frame["type"] != "trace"]


def _signed_in(session: VisitorSession, token: str = fake_mcp.GOOD_TOKEN) -> None:
    grant = Grant(
        access_token=token,
        refresh_token=None,
        expires_at=time.time() + 3600,
        scope="fan.read moot.write predict.write fan.write",
        client=RegisteredClient("test", "static"),
    )
    session.holder = GrantHolder(session.oauth, grant)
    session.auth_mode = "linked"


async def test_a_turn_calls_a_tool_shows_its_view_and_answers(mcp_server: RunningServer) -> None:
    answer = "Arsenal are two-one up. Want to hear what fans are saying?"
    llm = ScriptedLlm.of(call("live_scores", limit=3), say(answer))
    session = _session(mcp_server, llm)
    try:
        frames = await _turn(session, "what's the score?")
    finally:
        await session.close()

    assert _types(frames) == [
        "turn.start",
        "tool.start",
        "view.resource",
        "view",
        "tool.end",
        # Streamed word by word, as the provider sent it.
        *["text.delta"] * len(answer.split()),
        "turn.end",
    ]
    by_type = {frame["type"]: frame for frame in frames}
    assert by_type["tool.start"]["name"] == "live_scores"
    assert by_type["tool.start"]["args"] == {"limit": 3}
    # The server's own view, read over MCP and sent once.
    assert by_type["view.resource"]["uri"] == "ui://fanmoot/scoreboard"
    assert by_type["view.resource"]["html"] == fake_mcp.VIEW_HTML
    assert by_type["view"]["result"]["structuredContent"]["matches"][0]["home"] == "Arsenal"
    assert by_type["tool.end"]["ok"] is True
    assert by_type["tool.end"]["summary"].startswith("Arsenal are two-one up")

    end = by_type["turn.end"]
    assert end["status"] == "ok"
    assert end["text"] == answer
    # W1-10: the model that answered is on the turn, with its usage.
    assert end["model"] == "scripted/primary"
    assert end["usage"]["calls"] == 2
    assert end["tools"] == ["live_scores"]
    # And the MCP frames went out alongside.
    trace_methods = [f["frame"]["method"] for f in frames if f["type"] == "trace"]
    assert "tools/call" in trace_methods and "resources/read" in trace_methods


async def test_the_tools_offered_are_the_servers_own(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(say("Hello."))
    session = _session(mcp_server, llm)
    try:
        await _turn(session, "hi")
    finally:
        await session.close()

    offered = set(llm.requests[0].tools_dict)
    # From tools/list at runtime — minus the one the MCP Apps `visibility`
    # reserves for a view.
    assert offered == {
        "live_scores",
        "upcoming_matches",
        "list_matches",
        "read_thread",
        "find_club",
        "post_moot",
        "whoami",
        "model_only_hint",
    }
    declaration = llm.requests[0].tools_dict["live_scores"]._get_declaration()
    assert declaration.parameters_json_schema["properties"]["limit"]["type"] == "integer"


async def test_the_instruction_carries_the_persona_the_server_and_the_fan(
    mcp_server: RunningServer,
) -> None:
    llm = ScriptedLlm.of(say("Hello."))
    session = _session(mcp_server, llm)
    try:
        await _turn(session, "hi")
    finally:
        await session.close()

    instruction = str(llm.requests[0].config.system_instruction)
    assert "smart display" in instruction
    assert "read back exactly what will go out" in instruction
    # The server's own instructions, verbatim — braces and all.
    assert fake_mcp.INSTRUCTIONS in instruction
    assert "The fan is not signed in" in instruction
    # Signed out, a confirmed post is still *called*: the server's 401 is what
    # starts the sign-in (S4-9). Told "sign in first", a real model called
    # nothing and the sign-in never appeared (found in W4's browser run).
    assert "call the tool" in instruction
    assert "Never tell them to sign in instead of calling it" in instruction


async def test_the_model_reads_what_the_server_wrote_for_models(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(call("find_club", name="the gunners"), say("Arsenal."))
    session = _session(mcp_server, llm)
    try:
        await _turn(session, "who are the gunners?")
    finally:
        await session.close()

    response = llm.requests[1].contents[-1].parts[0].function_response
    assert response is not None
    assert response.response == {
        "say": "Arsenal, The Gunners.",
        "detail": "For follow-up tool calls only — ids and links, never read aloud:\n"
        '{"club": {"slug": "arsenal"}}',
    }


async def test_a_write_without_a_grant_asks_for_sign_in(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(
        call("post_moot", body="Saka was offside"),
        say("You'll need to connect your Fanmoot account first."),
    )
    session = _session(mcp_server, llm)
    try:
        frames = await _turn(session, "tell them Saka was offside")
    finally:
        await session.close()

    required = next(frame for frame in frames if frame["type"] == "auth.required")
    assert required["status"] == 401
    assert required["tool"] == "post_moot"
    assert required["sign_in_url"].startswith("/oauth/start?ticket=")
    end_of_tool = next(frame for frame in frames if frame["type"] == "tool.end")
    assert end_of_tool["ok"] is False and end_of_tool["reason"] == "auth_required"
    told = llm.requests[1].contents[-1].parts[0].function_response
    assert told is not None and told.response["error"] == "sign_in_required"
    # The turn itself still ends cleanly.
    assert frames[-1]["type"] == "turn.end" and frames[-1]["status"] == "ok"


async def test_a_signed_in_fan_can_post(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(call("post_moot", body="Saka was offside"), say("Posted."))
    session = _session(mcp_server, llm)
    _signed_in(session)
    try:
        frames = await _turn(session, "yes, post it")
    finally:
        session.holder = None  # nothing to revoke against the fake server
        await session.close()

    end_of_tool = next(frame for frame in frames if frame["type"] == "tool.end")
    assert end_of_tool["ok"] is True
    assert end_of_tool["summary"] == "Posted. It's live."
    assert "Signed in to Fanmoot" not in str(llm.requests[0].config.system_instruction)
    assert "The fan is signed in to Fanmoot" in str(llm.requests[0].config.system_instruction)


async def test_the_demo_account_is_rationed(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(call("post_moot", body="take"), say("That's the limit."))
    session = _session(mcp_server, llm, demo_fan_writes_per_session=1)
    _signed_in(session)
    session.auth_mode = "demo"
    session.demo_writes = 1
    sent_before = mcp_server.calls.count("tools/call")
    try:
        frames = await _turn(session, "post another")
    finally:
        session.holder = None
        await session.close()

    end_of_tool = next(frame for frame in frames if frame["type"] == "tool.end")
    assert end_of_tool["reason"] == "demo_limit"
    # Refused here: the server was never asked.
    assert mcp_server.calls.count("tools/call") == sent_before


async def test_the_conversation_is_remembered_between_turns(mcp_server: RunningServer) -> None:
    llm = ScriptedLlm.of(say("Arsenal."), say("Tonight at eight."))
    session = _session(mcp_server, llm)
    try:
        await _turn(session, "who are the gunners?")
        await _turn(session, "when do they play?")
    finally:
        await session.close()

    history = [
        part.text
        for content in llm.requests[1].contents
        for part in content.parts or []
        if part.text
    ]
    assert "who are the gunners?" in history and "Arsenal." in history


async def test_no_model_is_an_error_the_visitor_is_told(mcp_server: RunningServer) -> None:
    session = _session(mcp_server, ScriptedLlm.of(), model_chain="gemini/gemini-3.1-flash-lite")
    try:
        frames = await _turn(session, "score?")
    finally:
        await session.close()

    error = next(frame for frame in frames if frame["type"] == "error")
    assert error["code"] == "no_model"
    assert frames[-1]["type"] == "turn.end"
    assert frames[-1]["status"] == "error"
    assert frames[-1]["skipped"] == [
        {"model": "gemini/gemini-3.1-flash-lite", "reason": "no FMD_GEMINI_API_KEY"}
    ]


class _Hanging(BaseLlm):
    model: str = "hanging"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        await asyncio.sleep(60)
        yield LlmResponse()


async def test_a_turn_can_be_cancelled(mcp_server: RunningServer) -> None:
    session = _session(mcp_server, _Hanging())
    queue = session.outbox.attach()
    try:
        assert session.start_turn("score?") is None
        await asyncio.sleep(0.3)
        assert session.start_turn("again?") == "busy"
        assert await session.cancel_turn() is True
    finally:
        await session.close()

    frames = []
    while not queue.empty():
        frames.append(queue.get_nowait())
    end = next(frame for frame in frames if frame["type"] == "turn.end")
    assert end["status"] == "cancelled"


async def test_the_turn_cap_holds(mcp_server: RunningServer) -> None:
    session = _session(mcp_server, ScriptedLlm.of(say("One.")), session_turn_cap=1)
    try:
        await _turn(session, "one")
        assert session.start_turn("two") == "turn_cap"
    finally:
        await session.close()


@pytest.mark.parametrize("mode", ["none", "demo", "linked"])
def test_identity_lines_are_honest(mode: str) -> None:
    from app.agent.instructions import identity_line

    line = identity_line(mode, handle="alexa_demo", label="Demo fan account", scopes=["moot.write"])

    if mode == "demo":
        assert "shared account" in line and "@alexa_demo" in line
    elif mode == "linked":
        assert "signed in to Fanmoot as @alexa_demo" in line
    else:
        assert "not signed in" in line
