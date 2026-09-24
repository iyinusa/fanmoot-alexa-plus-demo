"""A small, real MCP server for the offline suite.

Built with the SDK's own FastMCP and served over real Streamable HTTP by
uvicorn on a loopback port, so the demo's client is exercised end to end —
``initialize``, ``tools/list``, ``tools/call``, ``resources/read``, session ids,
the GET stream and the closing DELETE — without the network.

It copies the four things about ``fanmoot-mcp`` the demo depends on:

* tools answer with a spoken sentence, a follow-up block, and
  ``structuredContent``;
* view tools carry ``_meta.ui.resourceUri`` and the view is a
  ``text/html;profile=mcp-app`` resource;
* write tools are annotated ``readOnlyHint: false``;
* a gate in front of the session manager answers a ``tools/call`` for a tool
  that needs a grant with **401** and an RFC 9728 challenge, or **403**
  ``insufficient_scope`` for a token without the scope — as an HTTP status,
  before the SDK sees the frame, exactly as ``app/oauth/bearer.py`` does there.

Its instructions contain braces on purpose: ADK treats an instruction string as
a template, and the real server's instructions contain braces too.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: Tokens the gate knows. "good" has every scope; "reader" only fan.read. An
#: access token minted by `tests/fake_as.py` ("at-…") is good too, so a sign-in
#: can be followed all the way to an authenticated call.
GOOD_TOKEN = "good-token"
READER_TOKEN = "reader-token"


def _known(token: str) -> bool:
    return token in (GOOD_TOKEN, READER_TOKEN) or token.startswith("at-")


INSTRUCTIONS = "Fake Fanmoot. Say scorelines out loud. Templating must not touch {this}."

VIEW_HTML = "<!doctype html><title>scoreboard</title><div id=app></div>"

_NEEDS: dict[str, str] = {"post_moot": "moot.write", "whoami": "fan.read"}


def _answer(say: str, detail: dict[str, Any], structured: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text=say),
            TextContent(
                type="text",
                text="For follow-up tool calls only — ids and links, never read aloud:\n"
                + json.dumps(detail),
            ),
        ],
        structuredContent=structured,
    )


def build_server() -> FastMCP:
    mcp = FastMCP("fanmoot-fake", instructions=INSTRUCTIONS)
    read_only = ToolAnnotations(readOnlyHint=True)

    @mcp.tool(
        name="live_scores",
        description="Everything in play right now.",
        annotations=read_only,
        meta={"ui": {"visibility": ["model", "app"], "resourceUri": "ui://fanmoot/scoreboard"}},
    )
    def live_scores(limit: int = 5) -> CallToolResult:
        return _answer(
            "Arsenal are two-one up at the Emirates, sixty-seventh minute.",
            {"matches": [{"id": "m1"}]},
            {"matches": [{"id": "m1", "home": "Arsenal", "away": "Spurs", "score": [2, 1]}]},
        )

    @mcp.tool(name="find_club", description="Resolve a club nickname.", annotations=read_only)
    def find_club(name: str) -> CallToolResult:
        return _answer(
            "Arsenal, The Gunners.", {"club": {"slug": "arsenal"}}, {"club": {"slug": "arsenal"}}
        )

    @mcp.tool(
        name="post_moot",
        description="Post a take as the signed-in fan.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
    )
    def post_moot(body: str) -> CallToolResult:
        return _answer("Posted. It's live.", {"moot": {"id": "p1"}}, {"held": False, "body": body})

    @mcp.tool(name="whoami", description="The signed-in fan.", annotations=read_only)
    def whoami() -> CallToolResult:
        return _answer("You're @demo_fan.", {}, {"handle": "demo_fan"})

    @mcp.tool(
        name="app_only_refresh",
        description="Called by a view, never by the model.",
        annotations=read_only,
        meta={"ui": {"visibility": ["app"]}},
    )
    def app_only_refresh() -> str:
        return "ok"

    @mcp.tool(
        name="model_only_hint",
        description="For the agent alone; a view may not call it.",
        annotations=read_only,
        meta={"ui": {"visibility": ["model"]}},
    )
    def model_only_hint() -> str:
        return "hint"

    @mcp.resource(
        "ui://fanmoot/scoreboard",
        name="scoreboard",
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"prefersBorder": False}},
    )
    def scoreboard() -> str:
        return VIEW_HTML

    return mcp


class Gate:
    """401 / 403 for a ``tools/call`` that needs a grant, before the SDK sees it."""

    def __init__(self, app: ASGIApp, base_url: str) -> None:
        self.app = app
        self.base_url = base_url
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        try:
            frame = json.loads(body)
        except ValueError:
            frame = {}
        self.requests.append({"frame": frame, "headers": headers})

        needed = None
        if isinstance(frame, dict) and frame.get("method") == "tools/call":
            needed = _NEEDS.get((frame.get("params") or {}).get("name", ""))
        if needed:
            token = headers.get("authorization", "").removeprefix("Bearer ")
            status, challenge = 0, ""
            metadata = f"{self.base_url}/.well-known/oauth-protected-resource/mcp"
            if not _known(token):
                status = 401
                challenge = f'Bearer resource_metadata="{metadata}"'
            elif token == READER_TOKEN and needed != "fan.read":
                status = 403
                challenge = (
                    f'Bearer error="insufficient_scope", scope="{needed}", '
                    f'resource_metadata="{metadata}"'
                )
            if status:
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [
                            (b"www-authenticate", challenge.encode()),
                            (b"content-type", b"application/json"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class RunningServer:
    def __init__(self, url: str, gate: Gate) -> None:
        self.url = url
        self.gate = gate

    @property
    def calls(self) -> list[str]:
        """Every JSON-RPC method the server was sent, in order."""
        return [
            r["frame"].get("method", "")
            for r in self.gate.requests
            if isinstance(r["frame"], dict) and "method" in r["frame"]
        ]


@contextmanager
def serve() -> Iterator[RunningServer]:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    mcp = build_server()
    gate = Gate(mcp.streamable_http_app(), base)
    config = uvicorn.Config(gate, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:  # pragma: no cover - a broken environment
            raise RuntimeError("fake MCP server did not start")
        time.sleep(0.02)
    try:
        yield RunningServer(f"{base}/mcp", gate)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
