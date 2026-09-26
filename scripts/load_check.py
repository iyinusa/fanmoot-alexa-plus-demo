"""Load-check the demo: N visitors at once, each holding a real MCP session (W6-2).

    make load-check                                          # 20 visitors, localhost:8090, 60 s
    make load-check URL=https://alexaplus.fanmoot.com
    make load-check URL=https://alexaplus.fanmoot.com ARGS="--turns 2"

Each visitor is what a browser tab is to the server: a ``/ws`` socket that
gets ``hello``, has an MCP session opened for it on the MCP server, receives
the ambient ticker, and pings every few seconds. They connect together, hold
together for ``--hold`` seconds, and leave together. With ``--turns`` each
also asks questions, which **spends model money** (about a cent a turn on
Nova Pro; see ``make turn-cost``) and counts against the per-address ceiling:
every visitor here shares your address, so keep visitors × turns under
``FMD_ADDRESS_TURNS_PER_HOUR``.

It reports what a judge would feel — time to the first frame, time until the
MCP session is up, ping round trips while all of them are connected, and turn
latency — and, with ``FMD_ADMIN_TOKEN`` set, what the server felt: sessions
held and resident memory, before, during and after.

Exits 1 if any visitor failed to connect, never got an MCP session, was
dropped, or was sent an error. Nothing is posted: no visitor signs in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

QUESTIONS = ("Who's playing in the next few days?", "What are fans arguing about right now?")


@dataclass
class Visitor:
    n: int
    hello_ms: float | None = None
    mcp_ms: float | None = None
    protocol: str | None = None
    pings: list[float] = field(default_factory=list)
    first_word_ms: list[float] = field(default_factory=list)
    turn_ms: list[float] = field(default_factory=list)
    turn_status: list[str] = field(default_factory=list)
    ambient: int = 0
    errors: list[str] = field(default_factory=list)
    dropped: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.hello_ms is not None
            and self.mcp_ms is not None
            and not self.errors
            and self.dropped is None
            and all(status == "ok" for status in self.turn_status)
        )


async def visit(visitor: Visitor, url: str, origin: str, hold_until: float, turns: int) -> None:
    started = time.perf_counter()
    pongs: dict[int, float] = {}
    turn_started = 0.0
    first_word = asyncio.Event()
    turn_done = asyncio.Event()
    mcp_ready = asyncio.Event()

    try:
        async with connect(url, origin=origin, open_timeout=20, max_size=2**22) as socket:  # type: ignore[arg-type]

            async def read() -> None:
                nonlocal turn_started
                async for raw in socket:
                    frame = json.loads(raw)
                    kind = frame.get("type")
                    now = time.perf_counter()
                    if kind == "hello":
                        visitor.hello_ms = (now - started) * 1000
                    elif kind == "facts" and (frame.get("mcp") or {}).get("protocol_version"):
                        if visitor.mcp_ms is None and frame.get("connected"):
                            visitor.mcp_ms = (now - started) * 1000
                            visitor.protocol = frame["mcp"]["protocol_version"]
                            mcp_ready.set()
                    elif kind == "pong" and frame.get("t") in pongs:
                        visitor.pings.append((now - pongs.pop(frame["t"])) * 1000)
                    elif kind == "ambient":
                        visitor.ambient += 1
                    elif kind == "text.delta" and not first_word.is_set():
                        visitor.first_word_ms.append((now - turn_started) * 1000)
                        first_word.set()
                    elif kind == "turn.end":
                        visitor.turn_ms.append((now - turn_started) * 1000)
                        visitor.turn_status.append(frame.get("status", "?"))
                        turn_done.set()
                    elif kind == "error":
                        visitor.errors.append(str(frame.get("code")))

            reader = asyncio.create_task(read())
            try:
                await asyncio.wait_for(mcp_ready.wait(), timeout=30)
            except TimeoutError:
                visitor.errors.append("no_mcp_session")

            for question in QUESTIONS[:turns]:
                first_word.clear()
                turn_done.clear()
                turn_started = time.perf_counter()
                await socket.send(json.dumps({"type": "turn", "text": question}))
                try:
                    await asyncio.wait_for(turn_done.wait(), timeout=90)
                except TimeoutError:
                    visitor.turn_status.append("timeout")

            seq = 0
            while time.perf_counter() < hold_until and not reader.done():
                seq += 1
                pongs[visitor.n * 1_000_000 + seq] = time.perf_counter()
                await socket.send(json.dumps({"type": "ping", "t": visitor.n * 1_000_000 + seq}))
                await asyncio.sleep(min(5.0, max(0.0, hold_until - time.perf_counter())))
            if reader.done() and (exc := reader.exception()) is not None:
                raise exc
            reader.cancel()
    except ConnectionClosed as exc:
        visitor.dropped = f"closed {exc.rcvd.code if exc.rcvd else '?'}"
    except Exception as exc:
        visitor.dropped = f"{type(exc).__name__}: {exc}"[:120]


def admin(base: str, token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        response = httpx.get(
            f"{base}/api/admin/models", headers={"Authorization": f"Bearer {token}"}, timeout=10
        )
        return response.json() if response.status_code == 200 else None
    except httpx.HTTPError:
        return None


def spread(values: list[float]) -> str:
    if not values:
        return "—"
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * len(ordered)) - 1)]
    median = statistics.median(ordered)
    return (
        f"p50 {median:7.0f} ms   p95 {p95:7.0f} ms   max {ordered[-1]:7.0f} ms   (n={len(ordered)})"
    )


def server_line(label: str, report: dict[str, Any] | None) -> str:
    if report is None:
        return f"  {label:<8} (set FMD_ADMIN_TOKEN to see the server's side)"
    process = report.get("process") or {}
    sessions = report.get("sessions", "?")
    return f"  {label:<8} sessions {sessions:>4}   rss {process.get('rss_mb', '?')} MB"


async def run(args: argparse.Namespace) -> int:
    base = args.url.rstrip("/")
    parts = urlsplit(base)
    ws_url = f"{'wss' if parts.scheme == 'https' else 'ws'}://{parts.netloc}/ws"
    origin = f"{parts.scheme}://{parts.netloc}"
    token = os.environ.get("FMD_ADMIN_TOKEN", "")

    before = admin(base, token)
    visitors = [Visitor(n) for n in range(args.visitors)]
    hold_until = time.perf_counter() + args.hold

    async def staggered(visitor: Visitor) -> None:
        await asyncio.sleep(visitor.n * args.stagger)
        await visit(visitor, ws_url, origin, hold_until, args.turns)

    tasks = [asyncio.create_task(staggered(v)) for v in visitors]
    await asyncio.sleep(min(args.hold / 2, 30))
    during = await asyncio.to_thread(admin, base, token)
    health_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            health = await client.get(f"{base}/healthz")
        health_line = (
            f"{health.status_code} in {(time.perf_counter() - health_started) * 1000:.0f} ms"
        )
    except httpx.HTTPError as exc:
        health_line = f"failed: {exc}"
    await asyncio.gather(*tasks)
    await asyncio.sleep(2)
    after = admin(base, token)

    ok = [v for v in visitors if v.ok]
    protocols = sorted({v.protocol for v in visitors if v.protocol})
    print(f"\nLoad check: {args.visitors} visitors against {base}, held {args.hold:.0f} s\n")
    print(f"  connected       {sum(v.hello_ms is not None for v in visitors)}/{args.visitors}")
    print(
        f"  MCP sessions    {sum(v.mcp_ms is not None for v in visitors)}/{args.visitors}"
        f"   protocol {', '.join(protocols) or '—'}"
    )
    print(f"  dropped         {sum(v.dropped is not None for v in visitors)}")
    print(f"  error frames    {sum(len(v.errors) for v in visitors)}")
    print(f"  ambient frames  {sum(v.ambient for v in visitors)}")
    print(f"  /healthz        {health_line}, under load\n")
    print(f"  first frame     {spread([v.hello_ms for v in visitors if v.hello_ms is not None])}")
    print(f"  MCP ready       {spread([v.mcp_ms for v in visitors if v.mcp_ms is not None])}")
    print(f"  ping RTT        {spread([p for v in visitors for p in v.pings])}")
    if args.turns:
        print(f"  first word      {spread([t for v in visitors for t in v.first_word_ms])}")
        print(f"  turn complete   {spread([t for v in visitors for t in v.turn_ms])}")
        statuses = [s for v in visitors for s in v.turn_status]
        print(f"  turns ok        {statuses.count('ok')}/{len(statuses)}")
    print("\n  Server")
    print(server_line("before", before))
    print(server_line("during", during))
    print(server_line("after", after))
    for visitor in visitors:
        if not visitor.ok:
            print(
                f"\n  visitor {visitor.n}: dropped={visitor.dropped} errors={visitor.errors} "
                f"turns={visitor.turn_status} mcp={'yes' if visitor.mcp_ms else 'no'}",
                end="",
            )
    verdict = "PASS" if len(ok) == args.visitors else "FAIL"
    print(f"\n\n{verdict}: {len(ok)}/{args.visitors} visitors held a clean session.")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost:8090", help="the demo's origin")
    parser.add_argument("--visitors", type=int, default=20)
    parser.add_argument("--hold", type=float, default=60.0, help="seconds all of them stay")
    parser.add_argument("--turns", type=int, default=0, choices=range(len(QUESTIONS) + 1))
    parser.add_argument("--stagger", type=float, default=0.05, help="seconds between arrivals")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
