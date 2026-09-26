"""A visitor's session: their MCP session, their identity, their conversation.

One per browser tab. It holds everything the browser must never hold (HAD-10):
the MCP session to ``mcp.fanmoot.com``, the fan's OAuth grant, and the agent's
memory of the conversation. The browser holds two random strings — a resume
token and a sign-in ticket — and everything else reaches it as frames.

**A session outlives its socket.** A phone at a judging table drops its
connection; a laptop sleeps. So a socket that closes *detaches* its session
rather than ending it, frames go into a small buffer, and a new socket that
presents the session id and resume token within ``FMD_SESSION_GRACE_S`` picks
up where the old one left off — the turn in flight, the conversation and the
sign-in intact. After the grace period the session is closed, its MCP session
ended and a visitor's own grant revoked, so nothing is left linked on the
server by a tab nobody will reopen.

**Identity changes reconnect.** Signing in and signing out each open a fresh
MCP session, so the ``initialize`` on the trace
is always the one this identity is using.

**Alexa's voice and ears** (``app.voice``) are the process's, shared by every
session, and each session keeps its own allowance of both: characters Polly
may speak for it, and seconds of the fan's voice Transcribe may hear. The
session frame says whether each is on, so the browser knows whether to use
them or its own Web Speech API.

**Two callers besides the agent (W2).** A card on screen may call a tool
through the host bridge — the thread view's reply composer — and the corner
ticker asks ``live_scores`` on its own schedule. Both go over this same MCP
session and this same grant, both are on the trace, tagged with who asked,
and neither is a way round the rules a turn obeys: a card's call must be one
the server marked callable by an app, and a write from a card needs the fan's
own grant exactly as the agent's would.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import secrets
import time
from collections import deque
from typing import Any

from mcp import types
from mcp.shared.exceptions import McpError

from app.agent import models
from app.agent.mcp_client import AuthChallenge, McpConnection, McpUnavailable
from app.agent.oauth import (
    PKCE_METHOD,
    DemoOAuthClient,
    GrantHolder,
    OAuthError,
    PendingAuthorization,
)
from app.agent.runtime import APP_NAME, SESSIONS, Turn, app_visible, view_uri
from app.agent.trace import Tracer
from app.agent.usage import TurnGate
from app.guided import GuideBoard
from app.logging_config import get_logger
from app.settings import Settings
from app.voice import Ears, Listening, Speaker, VoiceUnavailable

log = get_logger("session")

#: Frames kept for a detached session. Enough for a turn in flight; a visitor
#: gone longer than that is resuming a conversation, not a turn.
_DETACHED_BUFFER = 400

#: Frames queued for a slow socket before new ones are dropped.
_QUEUE_LIMIT = 2_000

#: The ticker's tools and arguments (W2-7). Few, because it is a corner of the
#: screen: three live scores fit, and so do the next three fixtures.
_TICKER_LIVE = ("live_scores", {"limit": 3})
_TICKER_NEXT = ("upcoming_matches", {"days": 7, "limit": 3})

#: What the ticker keeps of a match card. The rest — events, the spoken line
#: for that match — is for the scoreboard view, not a corner.
_TICKER_FIELDS = (
    "id",
    "status",
    "competition",
    "home",
    "away",
    "minute",
    "added",
    "period",
    "score_confirmed",
    "kickoff_at",
    "kickoff_spoken",
    "url",
)


class ViewCallRefused(Exception):
    """A card's ``tools/call`` the host will not make: a JSON-RPC error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _refused(text: str) -> dict[str, Any]:
    """A tool error in the tool's own shape, so a card shows the sentence as it
    shows any tool's refusal — "sign in to reply" beside the fan's words."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


class Outbox:
    """Frames for the visitor: to the socket when attached, to a buffer when not."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] | None = None
        self._buffer: deque[dict[str, Any]] = deque(maxlen=_DETACHED_BUFFER)

    @property
    def attached(self) -> bool:
        return self._queue is not None

    def attach(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        while self._buffer:
            queue.put_nowait(self._buffer.popleft())
        self._queue = queue
        return queue

    def detach(self, queue: asyncio.Queue[dict[str, Any]] | None = None) -> bool:
        """Stop sending to the socket. With ``queue``, only if it is still that
        socket's: a browser that resumed on a new socket before the old one
        closed must not be cut off when the old one finally does."""
        if queue is not None and queue is not self._queue:
            return False
        self._queue = None
        return True

    def send(self, frame: dict[str, Any]) -> None:
        if self._queue is None:
            self._buffer.append(frame)
            return
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning("outbox_full", frame_type=frame.get("type"))


class VisitorSession:
    def __init__(
        self,
        settings: Settings,
        *,
        oauth: DemoOAuthClient,
        connection_factory: Any = McpConnection,
        llm_factory: models.LlmFactory = models.build_llm,
        guide: GuideBoard | None = None,
        speaker: Speaker | None = None,
        ears: Ears | None = None,
    ) -> None:
        self.settings = settings
        self.oauth = oauth
        self.llm_factory = llm_factory
        self.guide = guide or GuideBoard(settings)
        self.speaker = speaker or Speaker(settings)
        self.ears = ears or Ears(settings)

        self.id = secrets.token_urlsafe(12)
        self.resume_token = secrets.token_urlsafe(24)
        self.ticket = secrets.token_urlsafe(24)
        self.outbox = Outbox()
        self.tracer = Tracer(
            max_payload_bytes=settings.trace_max_payload_bytes,
            sink=lambda frame: self.outbox.send({"type": "trace", "frame": frame}),
        )
        self.connection: McpConnection = connection_factory(
            settings.mcp_url,
            tracer=self.tracer,
            token_provider=self._token,
            timeout_s=settings.mcp_timeout_s,
        )

        # Who the fan is: "none", or "linked" to their own Fanmoot account.
        self.auth_mode = "none"
        self.holder: GrantHolder | None = None
        self.handle: str | None = None
        self.pending: dict[str, PendingAuthorization] = {}

        self.turns = 0
        # Alexa's voice and ears, spent (app.voice).
        self.voice_chars = 0
        self.listen_seconds = 0.0
        self.listening: Listening | None = None
        self._voice_tasks: set[asyncio.Task[None]] = set()
        self.turn_task: asyncio.Task[Any] | None = None
        self._adk_session: str | None = None
        self._views_sent: set[str] = set()
        self._view_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._connect_task: asyncio.Task[None] | None = None
        self.view_calls = 0
        self._view_tasks: set[asyncio.Task[None]] = set()
        self._ambient_task: asyncio.Task[None] | None = None
        self._ambient_wake = asyncio.Event()
        self._ambient_sent: str | None = None
        self._ambient_next: tuple[float, list[dict[str, Any]], str] | None = None
        # The guided scenarios (W4): the last frame sent, for a reconnecting
        # browser, and work a reset must stop — a reading of what is on, and
        # a post being read back from Fanmoot.
        self._scenarios_frame: dict[str, Any] | None = None
        self._guide_tasks: set[asyncio.Task[None]] = set()

        self.created = time.time()
        self.last_active = time.time()
        self.detached_at: float | None = None
        self.closed = False

    # ── Identity ─────────────────────────────────────────────────────────────
    @property
    def scopes(self) -> list[str]:
        return self.holder.scopes if self.holder else []

    async def _token(self) -> str | None:
        if self.holder is None:
            return None
        token = await self.holder.token(self.tracer)
        if token is None and self.auth_mode == "linked":
            # The server refused the refresh: the grant was revoked on
            # fanmoot.com's connected-apps page, or it lapsed. Say so.
            self.holder, self.auth_mode, self.handle = None, "none", None
            self.send_auth(reason="The Fanmoot sign-in has expired or was disconnected.")
        return token

    def auth_state(self) -> dict[str, Any]:
        return {
            "mode": self.auth_mode,
            "handle": self.handle,
            "scopes": self.scopes,
            # What the grant was obtained with, read from the grant rather
            # than asserted: only a session holding one says OAuth at all.
            "method": f"OAuth 2.1 + PKCE {PKCE_METHOD}" if self.holder else None,
            "client_mode": self.holder.grant.client.mode
            if self.holder and self.holder.grant
            else None,
        }

    def send_auth(self, *, reason: str | None = None) -> None:
        self.send({"type": "auth", **self.auth_state(), "reason": reason})
        self.send_facts()

    def auth_required(self, challenge: AuthChallenge, *, tool: str) -> None:
        self.send(
            {
                "type": "auth.required",
                "tool": tool,
                "status": challenge.status,
                "scope": challenge.scope,
                "sign_in_url": self.sign_in_url,
            }
        )

    @property
    def sign_in_url(self) -> str:
        return f"/oauth/start?ticket={self.ticket}"

    async def begin_sign_in(self, *, signup: bool = False) -> str:
        """The ``/authorize`` URL for this visitor's popup (W1-7). ``signup``
        opens it on the sign-up form, for the "New here" scenario (W4-5)."""
        self.touch()
        url, pending = await self.oauth.begin(self.tracer, signup=signup)
        self.pending = {s: p for s, p in self.pending.items() if not p.expired}
        self.pending[pending.state] = pending
        # A ticket opens one popup: the next sign-in gets a fresh one.
        self.ticket = secrets.token_urlsafe(24)
        self.send({"type": "session.ticket", "sign_in_url": self.sign_in_url})
        return url

    async def complete_sign_in(self, state: str, *, code: str, iss: str | None) -> None:
        pending = self.pending.pop(state, None)
        if pending is None or pending.expired:
            raise OAuthError("invalid_state", "That sign-in is not one this session started.")
        grant = await self.oauth.exchange(self.tracer, pending, code=code, iss=iss)
        previous, previous_mode = self.holder, self.auth_mode
        self.holder, self.auth_mode, self.handle = GrantHolder(self.oauth, grant), "linked", None
        if previous is not None and previous_mode == "linked":
            await previous.revoke(self.tracer)
        await self._switched("signed in to Fanmoot")

    async def sign_out(self) -> None:
        previous, previous_mode = self.holder, self.auth_mode
        self.holder, self.auth_mode, self.handle = None, "none", None
        if previous is not None and previous_mode == "linked":
            await previous.revoke(self.tracer)
        await self._switched("signed out")

    async def _switched(self, reason: str) -> None:
        try:
            await self.connection.reconnect(reason)
        except McpUnavailable:
            self.send_auth(reason=reason)
            return
        if self.holder is not None and self.handle is None:
            self.handle = await self._whoami()
        self.send_auth(reason=reason)

    async def _whoami(self) -> str | None:
        """Who the grant belongs to, asked of the server like anything else."""
        try:
            result = await self.connection.call_tool("whoami", {})
        except Exception:
            return None
        data = result.structuredContent or {}
        handle = data.get("handle")
        return str(handle) if handle else None

    # ── Alexa's voice (app.voice) ────────────────────────────────────────────
    async def speak(self, text: str) -> bytes:
        """One sentence of Alexa's, as MP3 from Polly, against this session's allowance."""
        text = " ".join(text.split())
        if self.voice_chars + len(text) > self.settings.voice_chars_per_session:
            raise VoiceUnavailable("voice_cap", "This session has said all it can out loud.")
        audio = await self.speaker.speak(text)
        self.voice_chars += len(text)
        return audio

    # ── Alexa's ears (app.voice.listen) ──────────────────────────────────────
    async def start_listening(self, listen_id: int, rate: int) -> None:
        """The mic opened: start relaying this utterance to Transcribe."""
        self.touch()
        await self.cancel_listening()

        def refuse(code: str, message: str) -> None:
            self.send({"type": "listen.error", "id": listen_id, "code": code, "message": message})

        if self.listen_seconds >= self.settings.listen_seconds_per_session:
            refuse("listen_cap", "I've listened all I can in this visit. Type instead.")
            return
        try:
            listening = self.ears.open(listen_id=listen_id, rate=rate, send=self.send)
        except VoiceUnavailable as exc:
            refuse(exc.code, exc.message)
            return
        self.listening = listening
        listening.task.add_done_callback(lambda _: self._listened(listening))

    def _listened(self, listening: Listening) -> None:
        self.listen_seconds += listening.seconds
        if self.listening is listening:
            self.listening = None

    def listen_audio(self, chunk: bytes) -> None:
        if self.listening is not None:
            self.listening.feed(chunk)

    def finish_listening(self, listen_id: int) -> None:
        """The fan stopped talking. The last words arrive as ``listen.final``,
        from a task, so the socket keeps reading while Transcribe finishes."""
        listening = self.listening
        if listening is None or listening.id != listen_id:
            return
        task = asyncio.create_task(listening.finish(), name=f"listen-finish-{self.id}")
        self._voice_tasks.add(task)
        task.add_done_callback(self._voice_tasks.discard)

    async def cancel_listening(self) -> None:
        listening, self.listening = self.listening, None
        if listening is not None:
            await listening.cancel()

    # ── Views (HAD-8) ────────────────────────────────────────────────────────
    async def send_view(
        self,
        uri: str,
        *,
        turn_id: str,
        call_id: str,
        tool: types.Tool,
        args: dict[str, Any],
        result: types.CallToolResult,
        label: str | None = None,
        link: str | None = None,
    ) -> None:
        """The server's own ``ui://`` view, and the payload it renders.

        The HTML is read over MCP (``resources/read``, on the trace) and sent
        once per session; each call then sends only the tool's input and
        result, which is what the view's app-bridge is handed (W2-6).
        """
        if uri not in self._views_sent:
            resource = await self._view_resource(uri)
            if resource is not None:
                self.send({"type": "view.resource", **resource})
                self._views_sent.add(uri)
        self.send(
            {
                "type": "view",
                "turn_id": turn_id,
                "call_id": call_id,
                "uri": uri,
                "tool": tool.name,
                "input": args,
                "result": result.model_dump(by_alias=True, mode="json", exclude_none=True),
                # What the card is, when it is not simply the tool's answer —
                # a post read back from Fanmoot (W4-4) — and where it lives.
                **({"label": label} if label else {}),
                **({"link": link} if link else {}),
            }
        )

    async def _view_resource(self, uri: str) -> dict[str, Any] | None:
        cached = self._view_cache.get(uri)
        if cached is not None and cached[0] == self.connection.generation:
            return cached[1]
        try:
            read = await self.connection.read_resource(uri)
        except Exception as exc:
            log.warning("view_unreadable", uri=uri, error=str(exc)[:200])
            return None
        for item in read.contents:
            if isinstance(item, types.TextResourceContents):
                payload = {
                    "uri": uri,
                    "mime": item.mimeType,
                    "html": item.text,
                    "meta": item.meta or {},
                }
                self._view_cache[uri] = (self.connection.generation, payload)
                return payload
        return None

    # ── A card calling a tool (W2-6) ─────────────────────────────────────────
    async def call_from_view(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A ``tools/call`` a card made through the host bridge.

        Returns the ``CallToolResult`` as JSON — a refusal the fan should read
        is a result with ``isError``, in the tool's own shape — or raises
        ``ViewCallRefused`` for what the host will not do at all.
        """
        self.touch()
        if self.view_calls >= self.settings.view_calls_per_session:
            raise ViewCallRefused(-32000, "This card has made all the calls it may in one session.")
        try:
            await self.connection.connect()
        except McpUnavailable as exc:
            raise ViewCallRefused(-32002, "Fanmoot could not be reached just now.") from exc
        tool = next((t for t in self.connection.tools if t.name == name), None)
        if tool is None:
            raise ViewCallRefused(-32602, f"The server has no tool called {name!r}.")
        if not app_visible(tool):
            # The spec's rule, not a preference: a tool the server kept for
            # the model is not a card's to call.
            raise ViewCallRefused(-32602, f"{name} is not callable from a card.")

        self.view_calls += 1
        try:
            result = await self.connection.call_tool(name, arguments, origin="view")
        except AuthChallenge as challenge:
            self.auth_required(challenge, tool=name)
            return _refused(
                f"That needs the {challenge.scope} permission. Sign in again to grant it."
                if challenge.status == 403
                else "Sign in to Fanmoot to do that — the button is on the screen."
            )
        except McpUnavailable as exc:
            raise ViewCallRefused(-32002, "Fanmoot could not be reached just now.") from exc
        except McpError as exc:
            raise ViewCallRefused(exc.error.code, exc.error.message) from exc

        return result.model_dump(by_alias=True, mode="json", exclude_none=True)

    def start_view_call(self, call_id: str | int, name: str, arguments: dict[str, Any]) -> None:
        """Run a card's call in the background and answer with
        ``view.call.result``, so the socket keeps reading while it runs."""

        async def run() -> None:
            frame: dict[str, Any] = {"type": "view.call.result", "id": call_id}
            try:
                frame["result"] = await self.call_from_view(name, arguments)
            except ViewCallRefused as exc:
                frame["error"] = {"code": exc.code, "message": exc.message}
            except Exception:
                log.exception("view_call_failed", tool=name)
                frame["error"] = {"code": -32603, "message": "That did not work just now."}
            self.send(frame)

        task = asyncio.create_task(run(), name=f"view-call-{self.id}-{call_id}")
        self._view_tasks.add(task)
        task.add_done_callback(self._view_tasks.discard)

    # ── The corner ticker (W2-7) ─────────────────────────────────────────────
    async def _ambient_loop(self) -> None:
        """Keep the corner of the screen alive before anyone speaks.

        Real ``live_scores`` calls on this session, on the trace as
        ``ambient``. Quiet while a turn runs (its frames are the ones worth
        reading) and while no socket is attached (nobody is looking). With
        nothing in play it shows the next fixtures instead, because a judge
        will most likely arrive on a weekday afternoon.
        """
        interval = self.settings.ambient_interval_s
        while not self.closed:
            delay = interval
            # Cleared before the look rather than after it, so an `attach`
            # that lands mid-look still earns an immediate second one.
            self._ambient_wake.clear()
            if self.outbox.attached and not self.busy:
                try:
                    delay = await self._ambient_tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.info("ambient_failed", error=str(exc)[:200])
                    delay = max(interval, 60.0)
            else:
                delay = min(interval, 5.0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._ambient_wake.wait(), timeout=delay)

    async def _ambient_tick(self) -> float:
        """One look at the scores. Returns how long until the next."""
        interval = self.settings.ambient_interval_s
        await self.connection.connect()
        names = {tool.name for tool in self.connection.tools}
        if _TICKER_LIVE[0] not in names:
            return max(interval, 300.0)

        live = await self.connection.call_tool(*_TICKER_LIVE, origin="ambient")
        if live.isError:
            return max(interval, 60.0)
        data = live.structuredContent or {}
        matches = _ticker_matches(data.get("matches"))
        if matches:
            self._send_ambient(live=True, matches=matches, spoken=_said(live, data))
            return interval

        # Nothing in play: the next fixtures, fetched far less often than the
        # live check, which keeps running so a kick-off shows within a minute.
        now = time.monotonic()
        cached = self._ambient_next
        if (
            cached is None or now - cached[0] > self.settings.ambient_idle_interval_s
        ) and _TICKER_NEXT[0] in names:
            upcoming = await self.connection.call_tool(*_TICKER_NEXT, origin="ambient")
            payload = upcoming.structuredContent or {}
            fixtures = [
                match
                for day in payload.get("days") or []
                if isinstance(day, dict)
                for match in _ticker_matches(day.get("matches"))
            ]
            cached = self._ambient_next = (now, fixtures, _said(upcoming, payload))
        if cached is not None:
            self._send_ambient(live=False, matches=cached[1], spoken=cached[2])
        return max(interval * 2, 60.0)

    def _send_ambient(self, *, live: bool, matches: list[dict[str, Any]], spoken: str) -> None:
        frame = {"type": "ambient", "live": live, "matches": matches, "spoken": spoken}
        key = json.dumps(frame, sort_keys=True, default=str)
        if key == self._ambient_sent:
            return  # unchanged: the corner stays as it is, the socket stays quiet
        self._ambient_sent = key
        self.send({**frame, "at": round(time.time() * 1000)})

    # ── Turns ────────────────────────────────────────────────────────────────
    def send(self, frame: dict[str, Any]) -> None:
        self.outbox.send(frame)

    def touch(self) -> None:
        self.last_active = time.time()

    @property
    def busy(self) -> bool:
        return self.turn_task is not None and not self.turn_task.done()

    @property
    def turns_left(self) -> int:
        return max(0, self.settings.session_turn_cap - self.turns)

    def start_turn(self, text: str) -> str | None:
        """Begin a turn. Returns the refusal code, or None when it started."""
        self.touch()
        if self.busy:
            return "busy"
        if self.turns_left <= 0:
            return "turn_cap"
        self.turns += 1
        turn_id = f"t{self.turns}"
        turn = Turn(self, turn_id, self.settings, factory=self.llm_factory)
        self.turn_task = asyncio.create_task(
            self._run_turn(turn, text), name=f"turn-{self.id}-{turn_id}"
        )
        return None

    async def _run_turn(self, turn: Turn, text: str) -> None:
        # `Turn.run` has already sent `turn.end` with status "cancelled".
        with contextlib.suppress(asyncio.CancelledError):
            await turn.run(text)

    async def cancel_turn(self) -> bool:
        task = self.turn_task
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        return True

    async def adk_session(self) -> str:
        if self._adk_session is None:
            created = await SESSIONS.create_session(app_name=APP_NAME, user_id=self.id)
            self._adk_session = created.id
        return self._adk_session

    async def reset(self) -> None:
        """Forget the conversation; keep the identity and the MCP session (W4-7).

        One tap, and it has to hold up to a judge pressing it at the worst
        moment: mid-answer, mid-read-back, twice in a row. So everything that
        could still put something on the screen is stopped *before* the screen
        is told to clear — the turn, and the read-back of a post — and nothing
        started for the old conversation arrives after ``session.reset``.
        The turn allowance is not refunded: it is the cost guard (HAD-9).
        """
        await self.cancel_turn()
        await self._cancel_guide_tasks()
        await self._drop_adk_session()
        self.tracer.emit(
            "session",
            direction="local",
            method="reset",
            summary="The visitor started over: the conversation is forgotten, "
            + ("the sign-in is kept" if self.holder is not None else "nobody was signed in"),
        )
        self.send({"type": "session.reset"})
        # The chips may be minutes old; a fresh start gets a fresh reading.
        self._spawn(self.send_scenarios(), name="scenarios")

    def _spawn(self, work: Any, *, name: str) -> None:
        task = asyncio.create_task(work, name=f"{name}-{self.id}")
        self._guide_tasks.add(task)
        task.add_done_callback(self._guide_tasks.discard)

    async def _cancel_guide_tasks(self) -> None:
        tasks = list(self._guide_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task

    # ── The guided scenarios (W4) ────────────────────────────────────────────
    async def send_scenarios(self) -> None:
        """The scenario chips, written from what is on (``app.guided``)."""
        try:
            scenarios, live_data = await self.guide.scenarios(self.connection)
        except Exception as exc:  # the chips are a convenience; never a crash
            log.info("scenarios_failed", error=str(exc)[:200])
            return
        frame = {
            "type": "scenarios",
            "live_data": live_data,
            "scenarios": [scenario.public() for scenario in scenarios],
        }
        self._scenarios_frame = frame
        self.send(frame)

    def show_live(self, take: dict[str, Any], *, turn_id: str, call_id: str) -> None:
        """W4-4: the take just posted, read back from Fanmoot, on the card
        stack beside the conversation.

        fanmoot.com cannot be framed (``frame-ancestors 'none'``), so "shown
        side by side" is the next best proof: a second, separate ``read_thread``
        on this session asks Fanmoot for the take by its id, and the server's
        own thread card renders what came back — with the take's fanmoot.com
        link one tap away. It runs beside the model's reply rather than in
        front of it, and a reset cancels it.
        """
        ref = take.get("url") or take.get("id")
        if not isinstance(ref, str) or not ref:
            return
        self._spawn(self._read_back(ref, take, turn_id=turn_id, call_id=call_id), name="live")

    async def _read_back(
        self, ref: str, take: dict[str, Any], *, turn_id: str, call_id: str
    ) -> None:
        tool = next((t for t in self.connection.tools if t.name == "read_thread"), None)
        if tool is None:
            return
        args = {"take": ref, "limit": 3}
        try:
            result = await self.connection.call_tool(tool.name, args, origin="verify")
        except Exception as exc:
            log.info("read_back_failed", error=str(exc)[:200])
            return
        uri = view_uri(tool)
        if result.isError or uri is None:
            return
        link = take.get("url") if isinstance(take.get("url"), str) else None
        await self.send_view(
            uri,
            turn_id=turn_id,
            call_id=f"{call_id}-live",
            tool=tool,
            args=args,
            result=result,
            label="Live on fanmoot.com",
            link=link,
        )

    async def _drop_adk_session(self) -> None:
        if self._adk_session is not None:
            try:
                await SESSIONS.delete_session(
                    app_name=APP_NAME, user_id=self.id, session_id=self._adk_session
                )
            except Exception:
                log.debug("adk_session_delete_failed")
            self._adk_session = None

    # ── Frames that describe the session ─────────────────────────────────────
    def state_frame(self, *, resumed: bool = False) -> dict[str, Any]:
        return {
            "type": "session",
            "session_id": self.id,
            "resume_token": self.resume_token,
            "resumed": resumed,
            "sign_in_url": self.sign_in_url,
            "auth": self.auth_state(),
            "voice": self.voice_state(),
            "turns_left": self.turns_left,
            "busy": self.busy,
        }

    def voice_state(self) -> dict[str, Any]:
        """Whether the browser should speak with Polly and listen with Transcribe."""
        settings = self.settings
        return {
            **self.speaker.describe(),
            "speak": self.speaker.ready and self.voice_chars < settings.voice_chars_per_session,
            "listen": self.ears.ready and self.listen_seconds < settings.listen_seconds_per_session,
            "language": settings.voice_language,
        }

    def send_facts(self) -> None:
        """The trace header strip's facts (W3-4), each read from the live session."""
        chain = models.current_chain(self.settings)
        facts = self.connection.facts
        discovered = self.oauth.discovered
        self.send(
            {
                "type": "facts",
                "mcp": facts.public() if facts else None,
                "connected": self.connection.connected,
                "auth": self.auth_state(),
                # What the authorization server advertises, from its own
                # metadata — known before anyone signs in.
                "oauth": discovered.public() if discovered else None,
                "models": {
                    "chain": [rung.qualified for rung in chain],
                    "primary": chain[0].qualified if chain else None,
                    "override": models.override_active(),
                },
            }
        )

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Open the MCP session in the background, so the facts are ready early."""
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = asyncio.create_task(self._connect(), name=f"connect-{self.id}")
        if self.settings.ambient_interval_s > 0 and (
            self._ambient_task is None or self._ambient_task.done()
        ):
            self._ambient_task = asyncio.create_task(
                self._ambient_loop(), name=f"ambient-{self.id}"
            )

    async def _connect(self) -> None:
        try:
            await self.connection.connect()
        except McpUnavailable as exc:
            log.warning("mcp_connect_failed", error=str(exc)[:200])
        self.send_facts()
        await self.send_scenarios()
        if self.connection.connected and self.oauth.discovered is None:
            # Discovery ahead of any sign-in, so the header strip's OAuth fact
            # is the server's own word (W3-4). Cached per process, so only the
            # first visitor in an hour sees these requests on their trace.
            try:
                await self.oauth.server_auth(self.tracer)
            except (OAuthError, ValueError, KeyError) as exc:
                log.info("oauth_discovery_skipped", error=str(exc)[:200])
            else:
                self.send_facts()

    def attach(self) -> asyncio.Queue[dict[str, Any]]:
        self.detached_at = None
        self.touch()
        # A browser that reconnects may be a reloaded page with an empty view
        # cache, so views are sent again on first use.
        self._views_sent.clear()
        # …and the ticker's state, now rather than at its next look.
        self._ambient_sent = None
        self._ambient_wake.set()
        queue = self.outbox.attach()
        # …and the scenario chips, which a reloaded page no longer has.
        if self._scenarios_frame is not None:
            self.send(self._scenarios_frame)
        return queue

    def detach(self, queue: asyncio.Queue[dict[str, Any]] | None = None) -> None:
        if self.outbox.detach(queue):
            self.detached_at = time.time()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.cancel_turn()
        if self._connect_task is not None and not self._connect_task.done():
            self._connect_task.cancel()
        for task in list(self._view_tasks):
            task.cancel()
        await self.cancel_listening()
        await self._cancel_guide_tasks()
        if self._ambient_task is not None and not self._ambient_task.done():
            self._ambient_task.cancel()
            with contextlib.suppress(BaseException):
                await self._ambient_task
        if self.holder is not None and self.auth_mode == "linked":
            await self.holder.revoke(self.tracer)
        self.holder = None
        await self.connection.close()
        await self._drop_adk_session()
        log.info("session_closed", session=self.id, turns=self.turns)


class SessionRegistry:
    """Every visitor session in this process, and the sweep that ends them."""

    def __init__(
        self,
        settings: Settings,
        *,
        oauth: DemoOAuthClient,
        connection_factory: Any = McpConnection,
        llm_factory: models.LlmFactory = models.build_llm,
        speaker: Speaker | None = None,
        ears: Ears | None = None,
    ) -> None:
        self.settings = settings
        self.oauth = oauth
        #: Alexa's voice and ears, one of each for every visitor (app.voice).
        self.speaker = speaker or Speaker(settings)
        self.ears = ears or Ears(settings)
        self.connection_factory = connection_factory
        self.llm_factory = llm_factory
        self.sessions: dict[str, VisitorSession] = {}
        self._sweeper: asyncio.Task[None] | None = None
        #: One reading of what is on, for every visitor (W4).
        self.guide = GuideBoard(settings)
        #: The ceilings on turns across sessions (W6-3).
        self.gate = TurnGate(settings)
        #: The count last broadcast, so an unchanged one is not sent again.
        self._presence_sent: int | None = None

    def create(self) -> VisitorSession | None:
        if len(self.sessions) >= self.settings.max_sessions:
            return None
        session = VisitorSession(
            self.settings,
            oauth=self.oauth,
            connection_factory=self.connection_factory,
            llm_factory=self.llm_factory,
            guide=self.guide,
            speaker=self.speaker,
            ears=self.ears,
        )
        self.sessions[session.id] = session
        return session

    def resume(self, session_id: str, token: str) -> VisitorSession | None:
        session = self.sessions.get(session_id)
        if session is None or session.closed:
            return None
        if not hmac.compare_digest(session.resume_token.encode(), token.encode()):
            return None
        return session

    def by_ticket(self, ticket: str) -> VisitorSession | None:
        for session in self.sessions.values():
            if not session.closed and hmac.compare_digest(session.ticket.encode(), ticket.encode()):
                return session
        return None

    def by_state(self, state: str) -> VisitorSession | None:
        for session in self.sessions.values():
            if not session.closed and state in session.pending:
                return session
        return None

    async def discard(self, session: VisitorSession) -> None:
        self.sessions.pop(session.id, None)
        await session.close()
        self.announce_presence()

    # ── Presence ─────────────────────────────────────────────────────────────
    def _online(self) -> list[VisitorSession]:
        """Sessions with a browser on the other end right now.

        A session kept for its grace period after its socket dropped is not
        counted: nobody is looking at it, and it may never be resumed.
        """
        return [s for s in self.sessions.values() if not s.closed and s.outbox.attached]

    def announce_presence(self, to: VisitorSession | None = None) -> None:
        """Tell every open page how many are online, when that number changes.

        ``to`` is a page that has just attached and needs the number even when
        it has not changed (a reload that resumed its old session).
        """
        online = self._online()
        frame = {"type": "presence", "online": len(online)}
        if len(online) != self._presence_sent:
            self._presence_sent = len(online)
            for session in online:
                session.send(frame)
        elif to is not None and not to.closed:
            to.send(frame)

    # ── Housekeeping ─────────────────────────────────────────────────────────
    def start_sweeper(self, interval_s: float = 30.0) -> None:
        async def sweep_forever() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    await self.sweep()
                except Exception:
                    log.exception("session_sweep_failed")

        self._sweeper = asyncio.create_task(sweep_forever(), name="session-sweeper")

    async def sweep(self) -> int:
        """Close sessions past their grace or idle limit. Returns how many."""
        now = time.time()
        expired = [
            session
            for session in self.sessions.values()
            if (
                session.detached_at is not None
                and now - session.detached_at > self.settings.session_grace_s
            )
            or (now - session.last_active > self.settings.session_idle_s and not session.busy)
        ]
        for session in expired:
            await self.discard(session)
        return len(expired)

    async def close_all(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
        for session in list(self.sessions.values()):
            await self.discard(session)


def _said(result: types.CallToolResult, data: dict[str, Any]) -> str:
    """The answer's spoken line: from the payload, or the first text block."""
    spoken = data.get("spoken")
    if isinstance(spoken, str) and spoken:
        return spoken
    return next((item.text for item in result.content if isinstance(item, types.TextContent)), "")


def _ticker_matches(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {key: match[key] for key in _TICKER_FIELDS if key in match}
        for match in value
        if isinstance(match, dict)
    ]


__all__ = [
    "Outbox",
    "SessionRegistry",
    "ViewCallRefused",
    "VisitorSession",
]
