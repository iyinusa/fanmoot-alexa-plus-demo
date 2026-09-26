"""One turn of the conversation: an ADK agent over the live MCP session (W1-2, W1-5).

**The tools come from ``tools/list``, at runtime, every turn.** Each MCP tool
the server advertises becomes an ADK tool here — its name, its description and
its JSON Schema exactly as the server sent them — and nothing about any tool is
written in this repository. When ``fanmoot-mcp`` gains a tool, the demo can use
it on the next turn; when one changes shape, the demo cannot be out of date.
The only filter is the one the MCP Apps extension defines: a tool whose
``_meta.ui.visibility`` leaves out ``model`` is for a view, not the agent.

**The agent is built per turn**, because two things it depends on change
between turns: the model chain (HAD-9, read per turn) and who the fan is (they
sign in mid-conversation). The *conversation* is not rebuilt: ADK's session
service holds the history under the visitor's session, so turn five still
remembers the derby from turn two.

**Everything a turn does becomes a frame** on the visitor's socket (W1-5):

``turn.start``   the turn has begun
``text.delta``   words, as the model streams them
``tool.start``   the agent called a tool — its name and arguments
``tool.end``     it answered — ok or not, how long, the spoken line
``view.resource`` the server's ``ui://`` view for that tool, once per session
``view``         the payload that view renders, for this call (HAD-8)
``auth.required`` the server asked for a grant — show "Sign in to Fanmoot"
``turn.end``     the whole answer, the model that gave it, its cost

and the MCP frames themselves travel alongside as ``trace`` (W1-6).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from google.adk.agents import LlmAgent, RunConfig

# The documented import path; ADK re-exports it without listing it in __all__.
from google.adk.agents.run_config import StreamingMode  # type: ignore[attr-defined]
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai
from mcp import types
from mcp.shared.exceptions import McpError

from app.agent import models
from app.agent.instructions import build_instruction, identity_line
from app.agent.mcp_client import AuthChallenge, McpUnavailable
from app.agent.models import FailoverLlm, LlmFactory, NoModelAvailable, TurnLedger
from app.logging_config import get_logger
from app.settings import Settings

if TYPE_CHECKING:
    from app.session import VisitorSession

log = get_logger("agent.runtime")

APP_NAME = "fanmoot_alexa_plus"

#: Every visitor's conversation history, keyed by their session. In memory: the
#: demo has no database and a conversation is not worth keeping past its tab.
SESSIONS = InMemorySessionService()

#: How much of a result's follow-up block the model is handed. The spoken line
#: always goes; past this the rest is left to the card on screen, because a
#: thread of twenty takes is a large prompt for a two-sentence answer.
MODEL_DATA_BUDGET = 8_000

#: Writes whose take is read back from Fanmoot once it is live (W4-4).
READ_BACK = frozenset({"post_moot", "reply_to_moot"})


def model_visible(tool: types.Tool) -> bool:
    """Whether the agent may call this tool (MCP Apps ``visibility``)."""
    ui = (tool.meta or {}).get("ui")
    visibility = ui.get("visibility") if isinstance(ui, dict) else None
    return visibility is None or "model" in visibility


def app_visible(tool: types.Tool) -> bool:
    """Whether a view may call this tool through the host (MCP Apps
    ``visibility``). The spec: the host "MUST reject" a view's ``tools/call``
    for a tool without ``app``. Absent means the default, which includes it."""
    ui = (tool.meta or {}).get("ui")
    visibility = ui.get("visibility") if isinstance(ui, dict) else None
    return visibility is None or "app" in visibility


def view_uri(tool: types.Tool) -> str | None:
    ui = (tool.meta or {}).get("ui")
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) else None


def is_write(tool: types.Tool) -> bool:
    """A tool that acts on Fanmoot, by the server's own annotation."""
    annotations = tool.annotations
    return annotations is not None and annotations.readOnlyHint is False


class McpBridgeTool(BaseTool):
    """One MCP tool, presented to ADK exactly as the server described it."""

    def __init__(self, tool: types.Tool, turn: Turn) -> None:
        super().__init__(name=tool.name, description=tool.description or tool.name)
        self._tool = tool
        self._turn = turn

    def _get_declaration(self) -> genai.FunctionDeclaration:
        return genai.FunctionDeclaration(
            name=self._tool.name,
            description=self._tool.description or self._tool.name,
            parameters_json_schema=self._tool.inputSchema,
        )

    async def run_async(self, *, args: dict[str, Any], tool_context: ToolContext) -> Any:
        return await self._turn.call_tool(self._tool, args, tool_context.function_call_id)


@dataclass
class TurnResult:
    turn_id: str
    status: str
    text: str
    ledger: TurnLedger
    ms: float
    tools: list[str]
    error: str | None = None


class Turn:
    """One question and its answer."""

    def __init__(
        self,
        session: VisitorSession,
        turn_id: str,
        settings: Settings,
        *,
        factory: LlmFactory = models.build_llm,
    ) -> None:
        self.session = session
        self.turn_id = turn_id
        self.settings = settings
        self.factory = factory
        self.ledger = TurnLedger()
        self.tools_called: list[str] = []

    # ── The turn ─────────────────────────────────────────────────────────────
    async def run(self, text: str) -> TurnResult:
        session = self.session
        started = time.perf_counter()
        spoken: list[str] = []
        session.send({"type": "turn.start", "turn_id": self.turn_id, "text": text})

        status, error = "ok", None
        runner: Runner | None = None
        try:
            await session.connection.connect()
            rungs = models.current_chain(self.settings)
            if not rungs:
                raise NoModelAvailable("FMD_MODEL_CHAIN is empty.")
            llm = FailoverLlm.for_turn(
                rungs,
                settings=self.settings,
                ledger=self.ledger,
                tracer=session.tracer,
                factory=self.factory,
            )
            instruction = build_instruction(
                session.connection.facts.instructions if session.connection.facts else None,
                identity_line(
                    session.auth_mode,
                    handle=session.handle,
                    scopes=session.scopes,
                ),
            )
            agent = LlmAgent(
                name="fanmoot_voice",
                model=llm,
                # A callable, not a string: ADK treats an instruction string
                # as a template, and the server's instructions contain braces.
                instruction=lambda _context: instruction,
                tools=[
                    McpBridgeTool(tool, self)
                    for tool in session.connection.tools
                    if model_visible(tool)
                ],
                generate_content_config=genai.GenerateContentConfig(
                    temperature=self.settings.model_temperature,
                    max_output_tokens=self.settings.model_max_output_tokens,
                ),
            )
            runner = Runner(app_name=APP_NAME, agent=agent, session_service=SESSIONS)
            adk_session = await session.adk_session()
            streamed = False
            async for event in runner.run_async(
                user_id=session.id,
                session_id=adk_session,
                new_message=genai.Content(role="user", parts=[genai.Part(text=text)]),
                run_config=RunConfig(
                    streaming_mode=StreamingMode.SSE,
                    max_llm_calls=self.settings.max_llm_calls_per_turn,
                ),
            ):
                words = _text_of(event)
                if event.partial:
                    if words:
                        streamed = True
                        session.send({"type": "text.delta", "turn_id": self.turn_id, "text": words})
                    continue
                if words:
                    # A model that does not stream sends its words only here;
                    # one that does has already sent them as deltas.
                    if not streamed:
                        session.send({"type": "text.delta", "turn_id": self.turn_id, "text": words})
                    spoken.append(words)
                streamed = False
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except NoModelAvailable as exc:
            status, error = "error", str(exc)
            session.send(
                {
                    "type": "error",
                    "code": "no_model",
                    "turn_id": self.turn_id,
                    "message": "No model could answer just now.",
                }
            )
        except McpUnavailable as exc:
            status, error = "error", str(exc)
            session.send(
                {
                    "type": "error",
                    "code": "mcp_unavailable",
                    "turn_id": self.turn_id,
                    "message": "The Fanmoot MCP server could not be reached.",
                }
            )
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"[:300]
            log.exception("turn_failed", turn_id=self.turn_id)
            session.send(
                {
                    "type": "error",
                    "code": "turn_failed",
                    "turn_id": self.turn_id,
                    "message": "Something went wrong answering that.",
                }
            )
        finally:
            if runner is not None:
                close = getattr(runner, "close", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:
                        log.debug("runner_close_failed")
            result = TurnResult(
                turn_id=self.turn_id,
                status=status,
                text=" ".join(part.strip() for part in spoken if part.strip()),
                ledger=self.ledger,
                ms=round((time.perf_counter() - started) * 1000, 1),
                tools=list(self.tools_called),
                error=error,
            )
            self._finish(result)
        return result

    def _finish(self, result: TurnResult) -> None:
        self.session.send(
            {
                "type": "turn.end",
                "turn_id": result.turn_id,
                "status": result.status,
                "text": result.text,
                "ms": result.ms,
                "tools": result.tools,
                **self.ledger.public(),
            }
        )
        # W1-10: the model id that answered, on every turn, in the log that
        # outlives the demo. `models` is every model that took part.
        log.info(
            "turn_complete",
            turn_id=result.turn_id,
            status=result.status,
            model=self.ledger.model,
            provider=self.ledger.provider,
            models=sorted({call.model for call in self.ledger.calls}),
            failovers=len(self.ledger.failovers),
            tools=result.tools,
            ms=result.ms,
            **self.ledger.totals(),
        )

    # ── Tools ────────────────────────────────────────────────────────────────
    async def call_tool(
        self, tool: types.Tool, args: dict[str, Any], call_id: str | None
    ) -> dict[str, Any]:
        """Run one MCP tool for the agent, and tell the visitor about it."""
        session = self.session
        call_id = call_id or f"{self.turn_id}-{len(self.tools_called)}"
        self.tools_called.append(tool.name)
        session.send(
            {
                "type": "tool.start",
                "turn_id": self.turn_id,
                "call_id": call_id,
                "name": tool.name,
                "title": tool.title,
                "args": args,
                "write": is_write(tool),
            }
        )
        started = time.perf_counter()

        def finished(ok: bool, summary: str, **extra: Any) -> None:
            session.send(
                {
                    "type": "tool.end",
                    "turn_id": self.turn_id,
                    "call_id": call_id,
                    "name": tool.name,
                    "ok": ok,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "summary": summary,
                    **extra,
                }
            )

        try:
            result = await self._call_with_one_reconnect(tool, args)
        except AuthChallenge as challenge:
            session.auth_required(challenge, tool=tool.name)
            say = (
                f"That needs the {challenge.scope} permission, which this sign-in did not grant."
                if challenge.status == 403
                else "That needs a linked Fanmoot account."
            )
            finished(False, say, reason="auth_required", http_status=challenge.status)
            return {
                "error": "sign_in_required",
                "say": say + " The sign-in button is on the screen.",
            }
        except McpUnavailable as exc:
            finished(False, str(exc), reason="unavailable")
            return {"error": "unavailable", "say": "Fanmoot could not be reached just now."}
        except McpError as exc:
            finished(False, exc.error.message, reason="rejected")
            return {"error": "rejected", "say": exc.error.message}

        say = _spoken(result)
        if result.isError:
            finished(False, say, reason="tool_error")
            return {"error": "tool_error", "say": say}

        if is_write(tool):
            structured = result.structuredContent or {}
            take = structured.get("take")
            # A held take is not live, and must not be shown as if it were
            # (HAD-7): only one Fanmoot published is read back.
            if tool.name in READ_BACK and isinstance(take, dict) and not structured.get("held"):
                session.show_live(take, turn_id=self.turn_id, call_id=call_id)
        uri = view_uri(tool)
        if uri is not None:
            await session.send_view(
                uri, turn_id=self.turn_id, call_id=call_id, tool=tool, args=args, result=result
            )
        finished(True, say, view=uri)
        return _for_model(result)

    async def _call_with_one_reconnect(
        self, tool: types.Tool, args: dict[str, Any]
    ) -> types.CallToolResult:
        """Call, and if the session had gone, reconnect and call once more.

        Only for a tool that changes nothing. A write whose session dropped
        mid-call may have happened; the server's idempotency key is per
        session, so a retry on a new session could post the take twice. That
        call fails honestly instead, and the fan can ask again.
        """
        connection = self.session.connection
        try:
            return await connection.call_tool(tool.name, args)
        except McpUnavailable:
            if is_write(tool):
                raise
            await connection.reconnect("the session dropped")
            return await connection.call_tool(tool.name, args)


def _text_of(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    return "".join(
        part.text
        for part in parts
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    )


def _spoken(result: types.CallToolResult) -> str:
    for item in result.content:
        if isinstance(item, types.TextContent):
            return item.text
    return ""


def _for_model(result: types.CallToolResult) -> dict[str, Any]:
    """What the agent reads back: the text blocks the server wrote for a model.

    ``fanmoot-mcp`` answers every tool with a sentence to say aloud and, after
    it, a block of ids and links "for follow-up tool calls only" — which is how
    the agent can reply to the take it just read out. That is the server's own
    judgement of what a model needs, so it is passed on as written.
    ``structuredContent`` is for the card on screen, not the prompt.
    """
    texts = [item.text for item in result.content if isinstance(item, types.TextContent)]
    out: dict[str, Any] = {"say": texts[0] if texts else ""}
    detail = "\n\n".join(texts[1:])
    if detail:
        out["detail"] = (
            detail
            if len(detail) <= MODEL_DATA_BUDGET
            else detail[:MODEL_DATA_BUDGET] + " … (the rest is on the card on screen)"
        )
    return out
