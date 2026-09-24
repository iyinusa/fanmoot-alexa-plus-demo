"""A real MCP session to ``mcp.fanmoot.com``, one per visitor (W1-1).

The official ``mcp`` client SDK over Streamable HTTP, negotiating ``2025-11-25``
exactly as Claude Desktop or Alexa+ would. The demo does not mock the server,
batch its calls or talk to Fanmoot's REST API behind its back: everything the
agent knows, it learned through this session, and every frame is on the trace.

Three things here are not the SDK's defaults, each for a reason.

**The streams are tapped.** ``streamable_http_client`` yields a read stream and
a write stream, and ``ClientSession`` sits on them. This module puts a pump on
each side, so every JSON-RPC message — requests, responses, notifications, in
both directions — passes through ``_record_out`` / ``_record_in`` on its way.
That is where the trace panel's frames and their round-trip timings come from
(W1-6, HAD-12), and it is the one place that sees all of them.

**A refused request is answered, not left to crash the session.** When the
server refuses a ``tools/call`` with HTTP 401 (no grant) or 403 (a grant
without the scope), ``mcp`` 1.30 raises ``HTTPStatusError`` inside the
transport's task group, which ends the *whole session* — every later call on it
times out. So ``_RefusalTransport`` sits under the SDK at the httpx layer: a
refused or failed POST of a JSON-RPC request comes back to the SDK as a JSON-RPC
error for that request's id, carrying the HTTP status and the
``WWW-Authenticate`` challenge. The SDK handles that like any other error, the
session survives, the agent gets ``AuthChallenge`` at once, and the visitor gets
a "Sign in to Fanmoot" button (W1-7). A 404 is left alone: that is the SDK's
own "session terminated" path.

**The bearer token is read per request.** ``_BearerAuth`` asks the session's
grant for a token on every HTTP request, so a token refreshed mid-session is
used from the next frame on. A *change of identity* — signing in, switching to
the demo fan, signing out — reconnects instead, and the new ``initialize`` is
on the trace: a session is bound to who opened it.

The session is owned by one supervisor task, because the SDK's transport is an
async context manager with a task group inside it and must be entered and left
by the same task. Everything else talks to the ``ClientSession`` it publishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from pydantic import AnyUrl

from app import __version__
from app.agent.trace import Tracer
from app.logging_config import get_logger

log = get_logger("agent.mcp")

TokenProvider = Callable[[], Awaitable[str | None]]

#: JSON-RPC codes for refusals that happened at the HTTP layer. Chosen from the
#: implementation-defined server-error range; they never reach the server, only
#: this process's own ``ClientSession``.
AUTH_REQUIRED = -32001
UPSTREAM_UNAVAILABLE = -32002

#: The MCP transport this client speaks. Stated once, and shown in the trace
#: panel's header strip because it is what the session actually used.
TRANSPORT = "Streamable HTTP"

_CHALLENGE_PARAM = re.compile(r'(\w+)="([^"]*)"')


class McpUnavailable(Exception):
    """The MCP server could not be reached, or the session has gone."""


@dataclass
class AuthChallenge(Exception):
    """The server refused a call for want of a grant or a scope (401 / 403)."""

    status: int
    scope: str | None = None
    error: str | None = None
    resource_metadata: str | None = None

    def __str__(self) -> str:
        if self.status == 403:
            return f"The Fanmoot grant does not include {self.scope or 'the scope this needs'}."
        return "This needs a linked Fanmoot account."

    @classmethod
    def from_error(cls, error: types.ErrorData) -> AuthChallenge:
        data = error.data if isinstance(error.data, dict) else {}
        params = dict(_CHALLENGE_PARAM.findall(str(data.get("www_authenticate") or "")))
        return cls(
            status=int(data.get("http_status") or 401),
            scope=params.get("scope"),
            error=params.get("error"),
            resource_metadata=params.get("resource_metadata"),
        )


@dataclass
class ServerFacts:
    """What the live session says about itself — the trace header strip (W3-4).

    Every field is read from the ``initialize`` exchange or its HTTP headers,
    never written here: a demo that asserted a protocol version the server no
    longer speaks would be the demo lying.
    """

    url: str
    protocol_version: str
    server_name: str
    server_version: str
    server_title: str | None
    instructions: str | None
    session_id: str | None
    transport: str = TRANSPORT
    connected_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "transport": self.transport,
            "protocol_version": self.protocol_version,
            "server_name": self.server_name,
            "server_title": self.server_title,
            "server_version": self.server_version,
            # A prefix only. The id is not a credential, but on an
            # unauthenticated session it is the handle to it.
            "session": (self.session_id or "")[:8] or None,
            "connected_at": round(self.connected_at * 1000),
        }


class _RefusalTransport(httpx.AsyncBaseTransport):
    """Answer a refused or unreachable JSON-RPC POST with a JSON-RPC error.

    Wraps the real transport. Only POSTs whose body is a JSON-RPC request are
    rewritten — a request has an id to answer. A refused *notification* gets a
    ``202``, which is what the SDK expects of a notification anyway, and the
    GET stream and the closing DELETE pass through untouched.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return await self._inner.handle_async_request(request)
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TransportError as exc:
            return _refusal(request, status=None, challenge="", reason=_describe(exc))
        if response.status_code < 400 or response.status_code == 404:
            return response
        await response.aread()
        await response.aclose()
        return _refusal(
            request,
            status=response.status_code,
            challenge=response.headers.get("www-authenticate", ""),
            reason=f"HTTP {response.status_code}",
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def _refusal(
    request: httpx.Request, *, status: int | None, challenge: str, reason: str
) -> httpx.Response:
    try:
        body = json.loads(request.content or b"null")
    except ValueError:
        body = None
    if not (isinstance(body, dict) and "id" in body and "method" in body):
        return httpx.Response(202, request=request)
    if status in (401, 403):
        code = AUTH_REQUIRED
        message = (
            "The server needs a linked Fanmoot account for this."
            if status == 401
            else "The Fanmoot grant does not include the scope this needs."
        )
    else:
        code = UPSTREAM_UNAVAILABLE
        message = f"The MCP server could not answer: {reason}"
    error = {
        "jsonrpc": "2.0",
        "id": body["id"],
        "error": {
            "code": code,
            "message": message,
            "data": {"http_status": status, "www_authenticate": challenge},
        },
    }
    return httpx.Response(200, json=error, request=request)


class _BearerAuth(httpx.Auth):
    """Attach the current access token, asking for it on every request."""

    def __init__(self, provider: TokenProvider | None) -> None:
        self._provider = provider

    async def async_auth_flow(self, request: httpx.Request) -> Any:
        token = await self._provider() if self._provider is not None else None
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        yield request


@dataclass
class _Pending:
    method: str
    started: float
    tool: str | None = None
    origin: str | None = None


class McpConnection:
    """One visitor's MCP session, supervised, traced and reconnectable."""

    def __init__(
        self,
        url: str,
        *,
        tracer: Tracer,
        token_provider: TokenProvider | None = None,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.tracer = tracer
        self.token_provider = token_provider
        self.timeout_s = timeout_s
        self._transport = transport

        self.facts: ServerFacts | None = None
        self.tools: list[types.Tool] = []
        #: Bumped on every (re)connect, so a caller can tell the session under
        #: it changed.
        self.generation = 0

        self._session: ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._error: BaseException | None = None
        self._stale = False
        self._lock = asyncio.Lock()
        self._pending: dict[str | int, _Pending] = {}
        #: Who asked for a ``tools/call`` that is about to go out, when it was
        #: not the agent: ``"view"`` (a card's own button) or ``"ambient"``
        #: (the corner ticker). Keyed by the call itself, because the SDK
        #: assigns the request id and the frame is recorded on another task.
        self._origins: dict[str, list[str]] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return (
            self._session is not None
            and self._task is not None
            and not self._task.done()
            and not self._stale
        )

    async def connect(self) -> None:
        """Open the session if it is not open. Safe to call from anywhere."""
        async with self._lock:
            if not self.connected:
                if self._task is not None:
                    await self._stop()
                await self._start("connect")

    async def reconnect(self, reason: str) -> None:
        """Close the session and open a fresh one — a new ``initialize``."""
        async with self._lock:
            await self._stop()
            await self._start("reconnect", reason=reason)

    async def close(self) -> None:
        async with self._lock:
            await self._stop()

    async def _start(self, method: str, *, reason: str = "") -> None:
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._error = None
        self._stale = False
        self._origins.clear()
        self.generation += 1
        self.tracer.emit(
            "session",
            direction="local",
            method=method,
            summary=(f"{reason} — " if reason else "")
            + f"opening a {TRANSPORT} session to {self.url}",
        )
        self._task = asyncio.create_task(self._run(), name=f"mcp-session-{self.generation}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.timeout_s)
        except TimeoutError:
            await self._stop()
            raise McpUnavailable(f"{self.url} did not complete initialize in time.") from None
        if self._session is None:
            error = self._error
            await self._stop()
            raise McpUnavailable(f"Could not open an MCP session to {self.url}.") from error

    async def _stop(self) -> None:
        task, self._task = self._task, None
        self._session = None
        if task is None:
            return
        self._closing.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        except Exception:
            log.debug("mcp_session_stop_error")

    async def _run(self) -> None:
        """The supervisor: owns the transport and the session for their life."""
        try:
            async with (
                httpx.AsyncClient(
                    auth=_BearerAuth(self.token_provider),
                    timeout=httpx.Timeout(self.timeout_s, read=300),
                    headers={"User-Agent": f"fanmoot-alexa-plus-demo/{__version__}"},
                    event_hooks={"request": [self._on_request], "response": [self._on_response]},
                    transport=_RefusalTransport(self._transport or httpx.AsyncHTTPTransport()),
                ) as http,
                streamable_http_client(self.url, http_client=http, terminate_on_close=True) as (
                    transport_read,
                    transport_write,
                    get_session_id,
                ),
            ):
                in_send, in_recv = anyio.create_memory_object_stream[SessionMessage | Exception](0)
                out_send, out_recv = anyio.create_memory_object_stream[SessionMessage](0)
                async with anyio.create_task_group() as pumps:
                    pumps.start_soon(self._pump_in, transport_read, in_send)
                    pumps.start_soon(self._pump_out, out_recv, transport_write)
                    async with ClientSession(
                        in_recv,
                        out_send,
                        read_timeout_seconds=timedelta(seconds=self.timeout_s),
                        client_info=types.Implementation(
                            name="fanmoot-alexa-plus-demo",
                            title="Fanmoot on Alexa+ (demo)",
                            version=__version__,
                        ),
                    ) as session:
                        init = await session.initialize()
                        self.tools = await self._list_tools(session)
                        self.facts = ServerFacts(
                            url=self.url,
                            protocol_version=str(init.protocolVersion),
                            server_name=init.serverInfo.name,
                            server_version=init.serverInfo.version,
                            server_title=getattr(init.serverInfo, "title", None),
                            instructions=init.instructions,
                            session_id=get_session_id(),
                        )
                        self._session = session
                        self._ready.set()
                        await self._closing.wait()
                    pumps.cancel_scope.cancel()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # an ExceptionGroup from the task groups, too
            self._error = exc
            log.warning("mcp_session_failed", error=_describe(exc))
            self.tracer.emit(
                "session",
                direction="local",
                method="session-error",
                status="error",
                summary=f"The MCP session ended: {_describe(exc)}",
            )
        finally:
            self._session = None
            if not self._closing.is_set():
                self._stale = True
            self._ready.set()

    @staticmethod
    async def _list_tools(session: ClientSession) -> list[types.Tool]:
        tools: list[types.Tool] = []
        cursor: str | None = None
        while True:
            page = await session.list_tools(
                params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
            )
            tools.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                return tools

    # ── Calls ────────────────────────────────────────────────────────────────
    async def _live(self) -> ClientSession:
        if not self.connected:
            await self.connect()
        if self._session is None:  # pragma: no cover - connect raises instead
            raise McpUnavailable("The MCP session is not open.")
        return self._session

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, origin: str | None = None
    ) -> types.CallToolResult:
        """Call a tool. ``origin`` tags its trace frames with who asked, when it
        was not the agent, so the trace panel can tell a card's button and the
        corner ticker from a turn — every one of them is still a real call."""
        session = await self._live()
        key = _call_key(name, arguments)
        if origin is not None:
            self._origins.setdefault(key, []).append(origin)
        try:
            return await session.call_tool(name, arguments)
        except McpError as exc:
            raise self._translate(exc) from None

    def _take_origin(self, params: dict[str, Any]) -> str | None:
        """The tag waiting for this call, first come first served.

        Two identical calls in flight together share a key, so a tagged and an
        untagged one could swap tags. The only pair that could is the ticker's
        ``live_scores`` beside the agent's, and the ticker waits while a turn
        runs (``app.session``), so in practice it does not happen."""
        key = _call_key(params.get("name"), params.get("arguments"))
        waiting = self._origins.get(key)
        if not waiting:
            return None
        origin = waiting.pop(0)
        if not waiting:
            del self._origins[key]
        return origin

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        session = await self._live()
        try:
            return await session.read_resource(AnyUrl(uri))
        except McpError as exc:
            raise self._translate(exc) from None

    def _translate(self, exc: McpError) -> Exception:
        if exc.error.code == AUTH_REQUIRED:
            return AuthChallenge.from_error(exc.error)
        if exc.error.code == UPSTREAM_UNAVAILABLE or self._stale:
            self._stale = True
            return McpUnavailable(exc.error.message)
        return exc

    # ── The taps ─────────────────────────────────────────────────────────────
    async def _pump_out(
        self,
        source: MemoryObjectReceiveStream[SessionMessage],
        sink: MemoryObjectSendStream[SessionMessage],
    ) -> None:
        async with source, sink:
            async for item in source:
                self._record_out(item.message)
                await sink.send(item)

    async def _pump_in(
        self,
        source: MemoryObjectReceiveStream[SessionMessage | Exception],
        sink: MemoryObjectSendStream[SessionMessage | Exception],
    ) -> None:
        async with source, sink:
            async for item in source:
                if isinstance(item, Exception):
                    # Nothing reaches here for a request any more (see
                    # _RefusalTransport); what does — a dropped GET stream,
                    # say — belongs to no call. Traced, and dropped as the SDK
                    # itself would.
                    self.tracer.emit(
                        "session",
                        direction="in",
                        method="transport",
                        status="error",
                        summary=_describe(item),
                    )
                    continue
                self._record_in(item.message)
                await sink.send(item)

    def _record_out(self, message: types.JSONRPCMessage) -> None:
        root = message.root
        if isinstance(root, types.JSONRPCRequest):
            params = root.params or {}
            tool = params.get("name") if root.method == "tools/call" else None
            origin = self._take_origin(params) if tool is not None else None
            self._pending[root.id] = _Pending(root.method, time.perf_counter(), tool, origin)
            self.tracer.emit(
                "mcp",
                direction="out",
                method=root.method,
                rpc_id=root.id,
                status="pending",
                summary=_request_summary(root.method, params),
                payload=root.model_dump(by_alias=True, mode="json", exclude_none=True),
                tool=tool,
                **_tagged(origin),
            )
        elif isinstance(root, types.JSONRPCNotification):
            self.tracer.emit(
                "mcp",
                direction="out",
                method=root.method,
                payload=root.model_dump(by_alias=True, mode="json", exclude_none=True),
            )
        else:  # this client answering a server request (a ping, say)
            self.tracer.emit(
                "mcp",
                direction="out",
                method="response",
                rpc_id=root.id,
                payload=root.model_dump(by_alias=True, mode="json", exclude_none=True),
            )

    def _record_in(self, message: types.JSONRPCMessage) -> None:
        root = message.root
        payload = root.model_dump(by_alias=True, mode="json", exclude_none=True)
        if isinstance(root, types.JSONRPCResponse):
            pending = self._pending.pop(root.id, None)
            method = pending.method if pending else "response"
            is_error = bool(root.result.get("isError")) if method == "tools/call" else False
            self.tracer.emit(
                "mcp",
                direction="in",
                method=method,
                rpc_id=root.id,
                status="error" if is_error else "ok",
                ms=_since(pending),
                summary=_response_summary(method, root.result),
                payload=payload,
                tool=pending.tool if pending else None,
                **_tagged(pending.origin if pending else None),
            )
        elif isinstance(root, types.JSONRPCError):
            pending = self._pending.pop(root.id, None)
            data = root.error.data if isinstance(root.error.data, dict) else {}
            if root.error.code == UPSTREAM_UNAVAILABLE:
                self._stale = True
            if "session terminated" in root.error.message.lower():
                # The server forgot this session (a redeploy, say). The next
                # call reconnects rather than failing the same way again.
                self._stale = True
            self.tracer.emit(
                "mcp",
                direction="in",
                method=pending.method if pending else "error",
                rpc_id=root.id,
                status="error",
                ms=_since(pending),
                summary=(
                    f"HTTP {data['http_status']} — {root.error.message}"
                    if data.get("http_status")
                    else root.error.message
                ),
                payload=payload,
                tool=pending.tool if pending else None,
                **_tagged(pending.origin if pending else None),
                http_status=data.get("http_status"),
                www_authenticate=data.get("www_authenticate") or None,
            )
        else:  # a notification or request from the server
            self.tracer.emit("mcp", direction="in", method=root.method, payload=payload)

    # ── HTTP hooks ───────────────────────────────────────────────────────────
    async def _on_request(self, request: httpx.Request) -> None:
        # Lines this demo's trace up with fanmoot-mcp's own log, which honours
        # an inbound X-Request-ID (HAD-12).
        request.headers.setdefault("X-Request-ID", uuid.uuid4().hex[:16])
        request.extensions["fm_started"] = time.perf_counter()

    async def _on_response(self, response: httpx.Response) -> None:
        request = response.request
        if request.method == "POST":
            return  # the JSON-RPC frames themselves are traced by the taps
        started = request.extensions.get("fm_started")
        ms = (time.perf_counter() - started) * 1000 if isinstance(started, float) else None
        what = {
            "GET": "the server-to-client event stream",
            "DELETE": "ending the session",
        }.get(request.method, request.method)
        self.tracer.emit(
            "session",
            direction="in",
            method=f"{request.method} {request.url.path}",
            status="ok" if response.status_code < 400 else "error",
            ms=ms,
            summary=f"{what} — HTTP {response.status_code}",
            http_status=response.status_code,
        )


# ── Helpers ──────────────────────────────────────────────────────────────────
def _call_key(name: Any, arguments: Any) -> str:
    return f"{name}:{json.dumps(arguments or {}, sort_keys=True, default=str)}"


def _tagged(origin: str | None) -> dict[str, Any]:
    """``origin`` on a frame only when someone other than the agent asked."""
    return {"origin": origin} if origin else {}


def _since(pending: _Pending | None) -> float | None:
    return None if pending is None else (time.perf_counter() - pending.started) * 1000


def _describe(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _describe(exc.exceptions[0])
    return f"{type(exc).__name__}: {exc}"[:300]


def _request_summary(method: str, params: dict[str, Any]) -> str:
    if method == "tools/call":
        return f"{params.get('name')}({json.dumps(params.get('arguments') or {})[:160]})"
    if method == "resources/read":
        return str(params.get("uri"))
    if method == "initialize":
        return f"protocol {params.get('protocolVersion')}"
    return method


def _response_summary(method: str, result: dict[str, Any]) -> str:
    if method == "initialize":
        info = result.get("serverInfo") or {}
        return (
            f"protocol {result.get('protocolVersion')} · {info.get('name')} {info.get('version')}"
        )
    if method == "tools/list":
        return f"{len(result.get('tools') or [])} tools"
    if method == "resources/read":
        contents = result.get("contents") or []
        return f"{len(contents)} content item(s)"
    if method == "tools/call":
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text"))[:240]
        return "no text"
    return "ok"
