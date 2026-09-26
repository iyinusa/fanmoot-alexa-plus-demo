"""W1-9: the agent completes a multi-turn scenario against the **live** server.

Deselected by default (``-m 'not live'`` in pyproject). ``make test-live``
runs it against ``https://mcp.fanmoot.com/mcp`` (or ``FMD_LIVE_MCP_URL``).

Two variants:

``test_scripted_scenario_against_the_live_server``
    A scripted model makes the calls ``app.agent.scenario`` names, so the
    assertions are exact: the tool calls in order, every one answered, the
    MCP handshake at ``2025-11-25``, and the unauthenticated post refused with a
    401 challenge and turned into a sign-in prompt. Needs no model credentials.
``test_real_model_scenario``
    ``FMD_LIVE_MODEL=1`` as well: the chain from the environment (Bedrock
    first) chooses its own tools. Asserted loosely — a real model may phrase
    and call differently — but it must answer every turn, call a Fanmoot tool
    for the factual ones, and never post without a grant.

Nothing is written to Fanmoot by either: the session is never signed in.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from app.agent.oauth import DemoOAuthClient
from app.agent.scenario import MATCHDAY
from app.session import VisitorSession
from app.settings import Settings
from tests.scripted import ScriptedLlm, call, say

pytestmark = pytest.mark.live

LIVE_MCP_URL = os.environ.get("FMD_LIVE_MCP_URL", "https://mcp.fanmoot.com/mcp")


def _session(settings: Settings, llm_factory: Any = None) -> VisitorSession:
    oauth = DemoOAuthClient(settings)
    kwargs = {"llm_factory": llm_factory} if llm_factory else {}
    return VisitorSession(settings, oauth=oauth, **kwargs)


async def _run(session: VisitorSession) -> list[list[dict[str, Any]]]:
    """Each turn's frames, in order."""
    queue = session.outbox.attach()
    turns: list[list[dict[str, Any]]] = []
    for turn in MATCHDAY:
        assert session.start_turn(turn.text) is None
        assert session.turn_task is not None
        await session.turn_task
        frames = []
        while not queue.empty():
            frames.append(queue.get_nowait())
        turns.append(frames)
    return turns


def _tools(frames: list[dict[str, Any]]) -> list[str]:
    return [frame["name"] for frame in frames if frame["type"] == "tool.start"]


def _trace(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [f["frame"] for frames in turns for f in frames if f["type"] == "trace"]


async def test_scripted_scenario_against_the_live_server() -> None:
    steps: list[dict[str, Any]] = []
    for turn in MATCHDAY:
        steps.extend(call(tool, **args) for tool, args in turn.calls)
        steps.append(say(turn.reply))
    llm = ScriptedLlm.of(*steps, model="scripted/live")
    settings = Settings(mcp_url=LIVE_MCP_URL, model_chain="scripted/live")
    session = _session(settings, lambda _rung, _settings: llm)
    try:
        turns = await _run(session)
    finally:
        await session.close()

    # The tool calls, turn by turn, in order.
    assert [_tools(frames) for frames in turns] == [
        [tool for tool, _args in turn.calls] for turn in MATCHDAY
    ]
    assert llm.remaining == 0

    trace = _trace(turns)
    mcp = [(f["dir"], f["method"]) for f in trace if f["kind"] == "mcp"]
    assert mcp[:5] == [
        ("out", "initialize"),
        ("in", "initialize"),
        ("out", "notifications/initialized"),
        ("out", "tools/list"),
        ("in", "tools/list"),
    ]
    handshake = next(f for f in trace if f["method"] == "initialize" and f["dir"] == "in")
    assert handshake["payload"]["result"]["protocolVersion"] == "2025-11-25"

    # Every read tool answered, and within the server's own budget's reach.
    answered = [f for f in trace if f["method"] == "tools/call" and f["dir"] == "in"]
    reads = [f for f in answered if f.get("tool") != "post_moot"]
    assert len(reads) == 3 and all(f["status"] == "ok" for f in reads)

    # The post without a grant: a 401 challenge, a sign-in prompt, no write.
    refused = next(f for f in answered if f.get("tool") == "post_moot")
    assert refused["http_status"] == 401
    assert "resource_metadata" in (refused["www_authenticate"] or "")
    assert any(frame["type"] == "auth.required" for frame in turns[-1])

    for frames in turns:
        assert frames[-1]["type"] == "turn.end"
        assert frames[-1]["status"] == "ok"


@pytest.mark.skipif(os.environ.get("FMD_LIVE_MODEL") != "1", reason="set FMD_LIVE_MODEL=1")
async def test_real_model_scenario() -> None:
    settings = Settings(mcp_url=LIVE_MCP_URL)
    session = _session(settings)
    try:
        turns = await _run(session)
    finally:
        await session.close()

    ends = [frames[-1] for frames in turns]
    assert all(end["type"] == "turn.end" and end["status"] == "ok" for end in ends)
    assert all(end["text"] for end in ends)
    # The factual turns went to Fanmoot for their facts.
    for index in (0, 1, 2):
        assert _tools(turns[index]), f"turn {index + 1} answered without a tool"
    # And nothing was posted without a grant.
    posted = [
        f
        for f in _trace(turns)
        if f["method"] == "tools/call" and f["dir"] == "in" and f.get("tool") == "post_moot"
    ]
    assert all(f["status"] == "error" for f in posted)
    print("models:", sorted({model for end in ends for model in end["models"]}))
