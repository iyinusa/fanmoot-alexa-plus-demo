"""Smoke-test the deployed demo over its public URL (W6-1).

    make smoke                                  # https://alexaplus.fanmoot.com
    make smoke URL=http://localhost:8090        # a local `make dev`

What a judge's browser meets, checked from the outside, and the one thing it
cannot see: that the deployment reaches the real MCP server.

1. TLS, HTTP → HTTPS, and the headers a public origin owes (HSTS, nosniff).
2. ``/healthz``: production, the SPA built, the MCP server reachable at
   ``2025-11-25``, a model ready, **and a Bedrock model ready** — the AWS
   Builder claim rests on it.
3. The page: the CSP, share tags that point at this origin (not localhost),
   the preview image, ``robots.txt``, gzip on the bundle, SPA fallback.
4. The OAuth client metadata document (SEP-991), naming this origin.
5. ``/sandbox`` can be framed by this origin only.
6. ``/ws``: a foreign ``Origin`` is refused; this origin gets ``hello``, and
   its session opens a real MCP session on the server, negotiating
   ``2025-11-25``.
7. The operator API is not open, and with ``FMD_ADMIN_TOKEN`` set, the cost
   guard (W6-3) is on: a demo-wide spend ceiling and turn ceiling.

Nothing is spent and nothing is created: no turn is asked, nobody signs in.
Warnings are printed but do not fail the run; every failure does.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

PROTOCOL = "2025-11-25"
failures: list[str] = []


def check(ok: bool, label: str, detail: str = "", *, warn: bool = False) -> bool:
    if ok:
        print(f"  PASS  {label}")
    else:
        print(f"  {'WARN' if warn else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
        if not warn:
            failures.append(label)
    return ok


async def socket_checks(base: str) -> None:
    ws_url = re.sub(r"^http", "ws", base) + "/ws"

    try:
        async with connect(ws_url, origin="https://example.com", open_timeout=10):  # type: ignore[arg-type]
            check(False, "a foreign Origin is refused", "the handshake was accepted")
    except InvalidStatus as exc:
        check(exc.response.status_code == 403, "a foreign Origin is refused (403)")
    except Exception as exc:
        check(False, "a foreign Origin is refused", f"{type(exc).__name__}: {exc}")

    frames: dict[str, dict[str, Any]] = {}
    try:
        async with connect(ws_url, origin=base, open_timeout=10, max_size=2**22) as socket:  # type: ignore[arg-type]

            async def until_mcp() -> None:
                async for raw in socket:
                    frame = json.loads(raw)
                    frames.setdefault(frame["type"], frame)
                    if frame["type"] == "facts" and frame.get("mcp"):
                        frames["facts"] = frame
                        return

            await asyncio.wait_for(until_mcp(), timeout=20)
    except Exception as exc:
        check(False, "/ws opens from this origin", f"{type(exc).__name__}: {exc}")
        return

    hello = frames.get("hello", {})
    check(bool(hello.get("session_id")), "/ws says hello", json.dumps(hello)[:200])
    check(
        hello.get("mcp_url") == "https://mcp.fanmoot.com/mcp",
        "…and drives https://mcp.fanmoot.com/mcp",
        str(hello.get("mcp_url")),
        warn=True,
    )
    facts = frames.get("facts", {})
    mcp = facts.get("mcp") or {}
    check(
        bool(facts.get("connected")) and mcp.get("protocol_version") == PROTOCOL,
        f"a visitor's session holds a live MCP session at {PROTOCOL}",
        f"connected={facts.get('connected')} protocol={mcp.get('protocol_version')}",
    )
    check(
        (frames.get("session") or {}).get("turns_left", 0) > 0,
        "the session has turns to spend",
        json.dumps(frames.get("session", {}))[:200],
    )


def run(base: str) -> int:
    http = httpx.Client(base_url=base, timeout=15.0, follow_redirects=False)
    https = base.startswith("https://")
    print(f"\n{base}\n")

    print("transport")
    try:
        health = http.get("/healthz")
    except httpx.HTTPError as exc:
        # Before the domain is routed, Traefik answers with its own default
        # certificate, and that is the whole story.
        check(False, "TLS handshake" if https else "reachable", f"{type(exc).__name__}: {exc}")
        print("\nFAIL: the deployment is not reachable. See docs/runbook.md, Deploy.")
        return 1
    if https:
        check(True, "TLS handshake")
        check(
            "max-age=" in health.headers.get("strict-transport-security", ""),
            "HSTS",
            health.headers.get("strict-transport-security", "absent"),
        )
        plain = httpx.get(base.replace("https://", "http://", 1) + "/", follow_redirects=False)
        check(
            plain.status_code in {301, 302, 307, 308}
            and plain.headers.get("location", "").startswith("https://"),
            "http:// redirects to https://",
            f"{plain.status_code} {plain.headers.get('location', '')}",
        )
    check(health.headers.get("x-content-type-options") == "nosniff", "nosniff")

    print("health")
    body = health.json() if health.status_code == 200 else {}
    check(health.status_code == 200, "/healthz answers 200", str(health.status_code))
    check(body.get("status") == "ok", "status ok", json.dumps(body.get("checks")))
    check(body.get("env") == "production", "FMD_ENV is production", str(body.get("env")))
    check(
        body.get("mcp_protocol_version") == PROTOCOL,
        f"the MCP server is reachable and speaks {PROTOCOL}",
        str(body.get("mcp_protocol_version")),
    )
    chain = body.get("model_chain") or []
    ready = [rung["model"] for rung in chain if rung.get("ready")]
    check(bool(ready), "at least one model is ready", json.dumps(chain))
    check(
        any(model.startswith("bedrock/") for model in ready),
        "a Bedrock model is ready (AWS Builder)",
        "no Bedrock rung has credentials: set FMD_AWS_* in Dokploy",
    )
    check(
        bool(chain) and chain[0]["model"].startswith("bedrock/"),
        "Bedrock answers first",
        f"the chain starts with {chain[0]['model'] if chain else 'nothing'}",
        warn=True,
    )
    check(bool(body.get("demo_fan")), "the demo fan account is configured", warn=True)

    print("page")
    page = http.get("/")
    html = page.text
    check(page.status_code == 200 and "text/html" in page.headers.get("content-type", ""), "/")
    csp = page.headers.get("content-security-policy", "")
    check("frame-ancestors 'none'" in csp, "CSP with frame-ancestors 'none'", csp or "absent")
    og_url = re.search(r'property="og:url" content="([^"]+)"', html)
    og_image = re.search(r'property="og:image" content="([^"]+)"', html)
    check(
        bool(og_url) and og_url.group(1).rstrip("/") == base,  # type: ignore[union-attr]
        "og:url is this origin",
        og_url.group(1) if og_url else "absent",
    )
    if og_image:
        image = httpx.get(og_image.group(1), timeout=15)
        check(
            og_image.group(1).startswith(base)
            and image.status_code == 200
            and image.headers.get("content-type") == "image/png",
            "og:image is this origin's, and loads",
            f"{og_image.group(1)} → {image.status_code}",
        )
    else:
        check(False, "og:image present")
    robots = http.get("/robots.txt")
    check(
        robots.status_code == 200 and "text/plain" in robots.headers.get("content-type", ""),
        "robots.txt",
    )
    script = re.search(r'<script[^>]+src="(/assets/[^"]+\.js)"', html)
    if script:
        bundle = http.get(script.group(1), headers={"Accept-Encoding": "gzip"})
        check(
            bundle.headers.get("content-encoding") == "gzip",
            "the bundle is gzipped",
            bundle.headers.get("content-encoding", "not compressed"),
        )
    else:
        check(False, "the page loads a bundle from /assets")
    linked = http.get("/linked")
    check(
        linked.status_code == 200 and "text/html" in linked.headers.get("content-type", ""),
        "SPA fallback (/linked)",
    )

    print("oauth client")
    metadata = http.get("/oauth/client-metadata.json")
    document = metadata.json() if metadata.status_code == 200 else {}
    check(
        document.get("client_id") == f"{base}/oauth/client-metadata.json",
        "client metadata names its own URL as client_id (SEP-991)",
        str(document.get("client_id")),
    )
    check(
        document.get("redirect_uris") == [f"{base}/oauth/callback"],
        "…and this origin's callback",
        str(document.get("redirect_uris")),
    )

    print("sandbox")
    sandbox = http.get("/sandbox")
    check(
        sandbox.status_code == 200
        and "frame-ancestors 'self'" in sandbox.headers.get("content-security-policy", ""),
        "/sandbox can be framed by this origin only",
    )

    print("socket")
    asyncio.run(socket_checks(base))

    print("operator")
    closed = http.get("/api/admin/models")
    check(
        closed.status_code in {401, 404},
        "/api/admin is closed without a token",
        str(closed.status_code),
    )
    token = os.environ.get("FMD_ADMIN_TOKEN", "")
    if token:
        report = http.get("/api/admin/models", headers={"Authorization": f"Bearer {token}"})
        usage = (report.json() if report.status_code == 200 else {}).get("usage", {})
        total = usage.get("total", {})
        check(report.status_code == 200, "the operator token works", str(report.status_code))
        check(bool(total.get("cap_usd")), "a daily spend ceiling is set", json.dumps(total))
        check(bool(total.get("turn_cap")), "a daily turn ceiling is set", json.dumps(total))
    else:
        print("  ----  cost guard not checked: set FMD_ADMIN_TOKEN to read it")

    print(f"\n{'FAIL' if failures else 'PASS'}: {len(failures)} failed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", nargs="?", default="https://alexaplus.fanmoot.com")
    return run(parser.parse_args().url.rstrip("/"))


if __name__ == "__main__":
    sys.exit(main())
