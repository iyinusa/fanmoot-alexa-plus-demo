"""What a turn costs, measured rather than guessed (W6-3).

    make turn-cost                          # the first rung of FMD_MODEL_CHAIN that can run
    make turn-cost CHAIN=gemini/gemini-3.1-flash-lite

Runs ``app.agent.scenario.MATCHDAY`` — the standing five-turn conversation —
with a **real** model against the live MCP server, through exactly the code a
visitor's turn uses, and reads the tokens off every model call. Then it prices
those same tokens for **every model in ``FMD_MODEL_CHAIN``**, so one run on
whichever provider has credentials answers "what would this cost on Nova Pro?
on Claude?" as well as "what did it cost?".

That is an estimate, and the report says so: tokenizers differ, so the same
prompt counts differently on each provider (within a few tens of percent for
these models). The ceilings the numbers feed are orders of magnitude, not
invoices.

It also fits the growth: a turn carries the conversation before it, so input
tokens climb turn on turn. The fitted line prices a visitor who uses every
turn ``FMD_SESSION_TURN_CAP`` allows, which is what the per-session ceiling
has to bound.

Nothing is posted: the session is never signed in, so the scenario's post ends
at the server's 401.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

import structlog

from app.agent import models
from app.agent.oauth import DemoOAuthClient
from app.agent.scenario import MATCHDAY
from app.agent.usage import estimate_usd
from app.logging_config import configure_logging
from app.session import VisitorSession
from app.settings import Settings, get_settings


async def measure(settings: Settings) -> list[dict[str, Any]]:
    """The `turn.end` frame of each scenario turn, in order."""
    oauth = DemoOAuthClient(settings)
    session = VisitorSession(settings, oauth=oauth)
    queue = session.outbox.attach()
    ends: list[dict[str, Any]] = []
    try:
        for turn in MATCHDAY:
            if session.start_turn(turn.text) is not None or session.turn_task is None:
                raise SystemExit("turn-cost: the session refused a turn.")
            await session.turn_task
            while not queue.empty():
                frame = queue.get_nowait()
                if frame["type"] == "turn.end":
                    ends.append(frame)
    finally:
        await session.close()
    return ends


def fit(points: list[int]) -> tuple[float, float]:
    """Least-squares ``a + b·n`` through ``points`` at n = 1, 2, …"""
    n = len(points)
    xs = range(1, n + 1)
    mean_x, mean_y = sum(xs) / n, sum(points) / n
    var = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, points, strict=True)) / var
    return mean_y - slope * mean_x, max(slope, 0.0)


def report(settings: Settings, measured: str, ends: list[dict[str, Any]]) -> str:
    tokens_in = [end["usage"]["tokens_in"] for end in ends]
    tokens_out = [end["usage"]["tokens_out"] for end in ends]
    calls = [end["usage"]["calls"] for end in ends]
    cap = settings.session_turn_cap
    a_in, b_in = fit(tokens_in)
    a_out, b_out = fit(tokens_out)
    session_in = sum(round(a_in + b_in * n) for n in range(1, cap + 1))
    session_out = sum(round(a_out + b_out * n) for n in range(1, cap + 1))
    mean_in, mean_out = sum(tokens_in) / len(ends), sum(tokens_out) / len(ends)

    lines = [
        f"Measured on {measured}, {len(ends)} turns against {settings.mcp_url}",
        "",
        f"{'turn':<6}{'model calls':>12}{'tokens in':>12}{'tokens out':>12}  tools",
    ]
    for end, n_in, n_out, n_calls in zip(ends, tokens_in, tokens_out, calls, strict=True):
        lines.append(
            f"{end['turn_id']:<6}{n_calls:>12}{n_in:>12,}{n_out:>12,}  "
            f"{', '.join(end['tools']) or '-'}"
        )
    lines += [
        "",
        f"Growth: {a_in:,.0f} + {b_in:,.0f}·n tokens in, per turn n.",
        f"A {cap}-turn session (FMD_SESSION_TURN_CAP): ~{session_in:,} in, ~{session_out:,} out.",
        "",
        "Priced for each model in FMD_MODEL_CHAIN (same tokens; tokenizers differ, so ±~30%):",
        "",
        f"{'model':<44}{'mean turn':>11}{'full session':>14}{'turns per $10':>15}",
    ]
    overrides = settings.model_price_overrides
    for rung in models.parse_chain(settings.model_chain):
        per_turn = estimate_usd(rung.qualified, round(mean_in), round(mean_out), overrides)
        per_session = estimate_usd(rung.qualified, session_in, session_out, overrides)
        if per_turn is None or per_session is None:
            lines.append(f"{rung.qualified:<44}{'no price':>11}")
            continue
        lines.append(
            f"{rung.qualified:<44}{f'${per_turn:.4f}':>11}{f'${per_session:.2f}':>14}"
            f"{f'{10 / per_turn:,.0f}' if per_turn else '-':>15}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chain", help="comma-separated model ids to measure on")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.env)
    # The report is the output; the turns' own log lines go to stderr, which
    # `make turn-cost` discards.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=logging.WARNING, force=True)
    runnable = [
        rung
        for rung in models.parse_chain(args.chain or settings.model_chain)
        if models.unconfigured_reason(rung, settings) is None
    ]
    if not runnable:
        print("turn-cost: no model in the chain has credentials here.", file=sys.stderr)
        return 1
    measured_on = runnable[0].qualified
    ends = asyncio.run(measure(settings.model_copy(update={"model_chain": measured_on})))
    failed = [end for end in ends if end["status"] != "ok"]
    if failed:
        print(
            f"turn-cost: turn {failed[0]['turn_id']} ended {failed[0]['status']}.", file=sys.stderr
        )
        return 1
    print(report(settings, measured_on, ends))
    return 0


if __name__ == "__main__":
    sys.exit(main())
