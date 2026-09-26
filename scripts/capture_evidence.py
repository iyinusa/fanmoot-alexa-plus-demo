"""Capture a Bedrock-only run as standing AWS Builder evidence (W1-10).

    make evidence                       # the Bedrock rungs of FMD_MODEL_CHAIN
    make evidence CHAIN=bedrock/us.amazon.nova-pro-v1:0

The AWS Builder mini challenge requires the AWS service to be *called*, not
named (HAD-9). So this runs ``app.agent.scenario.MATCHDAY`` — five turns
against the live MCP server — with the chain cut down to **Bedrock rungs only**,
through exactly the code a visitor's turn uses, and writes down what happened:

``evidence/bedrock-run-<UTC>.jsonl``
    Every frame the visitor's browser would have received — the turns, the
    words, the tool calls, and the redacted MCP, OAuth and model trace.
``evidence/bedrock-run-<UTC>.md``
    The summary a person reads: which Bedrock model answered each turn, its
    tokens and estimated cost, which tools it called, and the MCP handshake.

**It refuses to write evidence that is not evidence.** With no AWS credentials,
no Bedrock rung in the chain, or any turn answered by anything but Bedrock, it
exits non-zero and writes nothing — a transcript that quietly fell back to
Gemini would be a claim the file does not support. Nothing is posted to
Fanmoot: the session is never signed in, so the scenario's post ends at the
server's 401.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import __version__
from app.agent import models
from app.agent.oauth import DemoOAuthClient
from app.agent.scenario import MATCHDAY
from app.logging_config import configure_logging
from app.session import VisitorSession
from app.settings import get_settings

ROOT = Path(__file__).resolve().parent.parent


def _fail(message: str) -> int:
    print(f"evidence: {message}", file=sys.stderr)
    return 1


async def capture(
    chain: list[str], out: Path, *, llm_factory: models.LlmFactory = models.build_llm
) -> int:
    settings = get_settings().model_copy(update={"model_chain": ",".join(chain)})
    rungs = models.parse_chain(settings.model_chain)
    if not rungs or any(rung.provider != "bedrock" for rung in rungs):
        return _fail("the chain must be Bedrock rungs only.")
    reasons = [models.unconfigured_reason(rung, settings) for rung in rungs]
    if all(reasons):
        return _fail(f"no Bedrock rung can run: {reasons[0]}.")

    oauth = DemoOAuthClient(settings)
    session = VisitorSession(settings, oauth=oauth, llm_factory=llm_factory)
    queue = session.outbox.attach()
    frames: list[dict[str, Any]] = []
    ends: list[dict[str, Any]] = []
    try:
        for turn in MATCHDAY:
            if session.start_turn(turn.text) is not None or session.turn_task is None:
                return _fail("the session refused a turn.")
            await session.turn_task
            while not queue.empty():
                frame = queue.get_nowait()
                frames.append(frame)
                if frame["type"] == "turn.end":
                    ends.append(frame)
    finally:
        await session.close()

    failed = [end for end in ends if end["status"] != "ok"]
    if failed:
        return _fail(f"turn {failed[0]['turn_id']} did not complete: {failed[0]['status']}.")
    foreign = sorted(
        {model for end in ends for model in end["models"] if not model.startswith("bedrock/")}
    )
    if foreign or any(not end["models"] for end in ends):
        return _fail(f"a turn was answered by something other than Bedrock: {foreign}.")

    summary = _write(out, settings.mcp_url, frames, ends)
    print(f"evidence: wrote {summary} and its transcript")
    return 0


def _write(
    out: Path, mcp_url: str, frames: list[dict[str, Any]], ends: list[dict[str, Any]]
) -> Path:
    """Both files, written once the run has been checked. The run is over, so
    blocking file I/O here holds up nothing."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / f"bedrock-run-{stamp}.jsonl"
    jsonl.write_text("".join(json.dumps(frame, ensure_ascii=False) + "\n" for frame in frames))
    summary = out / f"bedrock-run-{stamp}.md"
    summary.write_text(_summary(stamp, mcp_url, frames, ends, jsonl.name))
    return summary


def _summary(
    stamp: str, mcp_url: str, frames: list[dict[str, Any]], ends: list[dict[str, Any]], jsonl: str
) -> str:
    trace = [f["frame"] for f in frames if f["type"] == "trace"]
    handshake = next((f for f in trace if f["method"] == "initialize" and f["dir"] == "in"), None)
    result = (handshake or {}).get("payload", {}).get("result", {})
    info = result.get("serverInfo", {})
    texts = {f["turn_id"]: f["text"] for f in frames if f["type"] == "turn.start"}
    total_in = sum(end["usage"]["tokens_in"] for end in ends)
    total_out = sum(end["usage"]["tokens_out"] for end in ends)
    costs = [end["usage"]["cost_usd"] for end in ends if end["usage"]["cost_usd"] is not None]

    lines = [
        f"# Bedrock run — {stamp}",
        "",
        "Standing AWS Builder evidence (W1-10): the demo's agent answering a five-turn",
        "matchday conversation against the live Fanmoot MCP server, with the model chain",
        "cut to Amazon Bedrock only. Captured by `make evidence`; every frame is in",
        f"[`{jsonl}`]({jsonl}).",
        "",
        f"- **Demo build:** fanmoot-alexa-plus-demo {__version__}",
        f"- **MCP server:** {mcp_url} — {info.get('name')} {info.get('version')}",
        f"- **Protocol negotiated:** `{result.get('protocolVersion')}` over Streamable HTTP",
        f"- **Models that answered:** {', '.join(sorted({m for e in ends for m in e['models']}))}",
        f"- **Tokens:** {total_in:,} in, {total_out:,} out"
        + (f" · estimated ${sum(costs):.4f}" if costs else ""),
        "",
        "| Turn | The fan said | Model | Tools called | Tokens (in → out) | ms |",
        "|---|---|---|---|---|---|",
    ]
    for end in ends:
        usage = end["usage"]
        lines.append(
            f"| {end['turn_id']} | {texts.get(end['turn_id'], '')} | `{end['model']}` "
            f"| {', '.join(end['tools']) or '—'} | {usage['tokens_in']} → {usage['tokens_out']} "
            f"| {end['ms']:.0f} |"
        )
    lines += ["", "## What the agent said", ""]
    for end in ends:
        lines.append(f"**{end['turn_id']}** — {texts.get(end['turn_id'], '')}  ")
        lines.append(f"> {end['text']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--chain", help="comma-separated Bedrock model ids (default: FMD_MODEL_CHAIN's)"
    )
    parser.add_argument("--out", default=str(ROOT / "evidence"), help="where to write the files")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.env)
    chain = (
        [name.strip() for name in args.chain.split(",") if name.strip()]
        if args.chain
        else [
            rung.qualified
            for rung in models.parse_chain(settings.model_chain)
            if rung.provider == "bedrock"
        ]
    )
    return asyncio.run(capture(chain, Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
