"""What the models cost, per provider, per day — and the ceilings on it (W1-3c).

The creator chose ADK so that "Bedrock got expensive, switch to Gemini" is a
configuration change. This module is what makes it a decision taken on numbers:
every model call is counted here with its tokens and its estimated spend, and
``GET /api/admin/models`` reads it back.

Estimates come from LiteLLM's own price table, which knows the default chain's
models, with ``FMD_MODEL_PRICES`` for anything it does not. A model with no
known price is recorded as **unknown**, never as free: an operator reading
"$0.00" beside a model nobody priced would draw exactly the wrong conclusion.

In memory, per process, and deliberately so. The demo has no database, runs one
worker (see the Dockerfile), and the ceilings exist to stop a judging-day spike
emptying an account within a day — a restart resetting them is acceptable; a
database added for this would not be.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.logging_config import get_logger

log = get_logger("agent.usage")


@dataclass
class ProviderDay:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    #: Calls whose model had no known price. Their cost is not in `cost_usd`.
    unpriced_calls: int = 0


def estimate_usd(
    model: str, tokens_in: int, tokens_out: int, overrides: dict[str, tuple[float, float]]
) -> float | None:
    """Estimated dollars for one call, or None when nobody knows the price."""
    if model in overrides:
        price_in, price_out = overrides[model]
        return (tokens_in * price_in + tokens_out * price_out) / 1_000_000
    try:
        import litellm  # noqa: PLC0415 - heavy, and only needed once a call has been made

        cost_in, cost_out = litellm.cost_per_token(
            model=model, prompt_tokens=tokens_in, completion_tokens=tokens_out
        )
    except Exception:
        return None
    return float(cost_in) + float(cost_out)


class UsageLedger:
    """Per-provider, per-UTC-day totals for the whole process."""

    def __init__(self) -> None:
        self._days: dict[tuple[str, str], ProviderDay] = {}

    @staticmethod
    def today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _day(self, provider: str) -> ProviderDay:
        key = (self.today(), provider)
        if key not in self._days:
            # Yesterday's rows are dropped as today's first call arrives: the
            # ceiling is per day, and the history is the log's job.
            self._days = {k: v for k, v in self._days.items() if k[0] == key[0]}
            self._days[key] = ProviderDay()
        return self._days[key]

    def record(self, provider: str, tokens_in: int, tokens_out: int, cost: float | None) -> None:
        day = self._day(provider)
        day.calls += 1
        day.tokens_in += tokens_in
        day.tokens_out += tokens_out
        if cost is None:
            day.unpriced_calls += 1
        else:
            day.cost_usd += cost

    def record_failure(self, provider: str) -> None:
        self._day(provider).failures += 1

    def spent_today(self, provider: str) -> float:
        return self._day(provider).cost_usd

    def over_cap(self, provider: str, caps: dict[str, float]) -> bool:
        cap = caps.get(provider)
        return cap is not None and self.spent_today(provider) >= cap

    def snapshot(self, caps: dict[str, float]) -> dict[str, Any]:
        today = self.today()
        providers = {
            provider: {
                **asdict(day),
                "cost_usd": round(day.cost_usd, 6),
                "cap_usd": caps.get(provider),
                "over_cap": provider in caps and day.cost_usd >= caps[provider],
            }
            for (day_key, provider), day in sorted(self._days.items())
            if day_key == today
        }
        return {"day": today, "providers": providers}


#: The process's ledger. One worker, so one ledger.
LEDGER = UsageLedger()
