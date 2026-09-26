"""What the models cost, per provider, per day — and the ceilings on it (W1-3c).

The creator chose ADK so that "Bedrock got expensive, switch to Gemini" is a
configuration change. This module is what makes it a decision taken on numbers:
every model call is counted here with its tokens and its estimated spend, and
``GET /api/admin/models`` reads it back.

Estimates come from LiteLLM's own price table, which knows the default chain's
models, with ``FMD_MODEL_PRICES`` for anything it does not. A model with no
known price is recorded as **unknown**, never as free: an operator reading
"$0.00" beside a model nobody priced would draw exactly the wrong conclusion.

Alexa's voice and ears are counted here too (``app.voice``): ``polly`` and
``transcribe`` rows, priced from AWS's lists, so they count against the same
ceilings. Their ``tokens_in`` is what those services bill by instead —
characters spoken, and seconds heard.

In memory, per process, and deliberately so. The demo has no database, runs one
worker (see the Dockerfile), and the ceilings exist to stop a judging-day spike
emptying an account within a day — a restart resetting them is acceptable; a
database added for this would not be.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.logging_config import get_logger

if TYPE_CHECKING:
    from app.settings import Settings

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
        #: Visitor turns started, per UTC day (W6-3). Only today's is kept.
        self._turns: dict[str, int] = {}

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

    def spent_total_today(self) -> float:
        """Every provider together, today. Unpriced calls are not in it."""
        today = self.today()
        return sum(day.cost_usd for (day_key, _), day in self._days.items() if day_key == today)

    def record_turn(self) -> None:
        today = self.today()
        self._turns = {today: self._turns.get(today, 0) + 1}

    def turns_today(self) -> int:
        return self._turns.get(self.today(), 0)

    def over_cap(self, provider: str, caps: dict[str, float]) -> bool:
        cap = caps.get(provider)
        return cap is not None and self.spent_today(provider) >= cap

    def snapshot(
        self, caps: dict[str, float], *, cap_usd: float = 0.0, turn_cap: int = 0
    ) -> dict[str, Any]:
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
        total = self.spent_total_today()
        return {
            "day": today,
            "providers": providers,
            # The demo-wide ceilings (W6-3). A cap of None is no ceiling.
            "total": {
                "turns": self.turns_today(),
                "turn_cap": turn_cap or None,
                "cost_usd": round(total, 6),
                "cap_usd": cap_usd or None,
                "over_cap": bool(
                    (cap_usd and total >= cap_usd) or (turn_cap and self.turns_today() >= turn_cap)
                ),
            },
        }

    def over_total_cap(self, cap_usd: float) -> bool:
        return bool(cap_usd) and self.spent_total_today() >= cap_usd


#: The process's ledger. One worker, so one ledger.
LEDGER = UsageLedger()


class TurnGate:
    """The public URL's ceilings on turns, checked before a turn starts (W6-3).

    The per-session cap (``FMD_SESSION_TURN_CAP``) bounds one tab, and a reload
    is a new tab. So three more, each for a way round the one before:

    * **per address, per hour** (``FMD_ADDRESS_TURNS_PER_HOUR``): a reload, or
      a script opening sockets, is still the same address. A sliding hour, so
      a judge who pauses gets their turns back without anyone resetting
      anything.
    * **turns per UTC day** (``FMD_DAILY_TURN_CAP``): many addresses. It also
      holds for a model LiteLLM has no price for, which the dollar ceilings
      cannot see.
    * **dollars per UTC day, all providers** (``FMD_DAILY_CAP_USD``): the
      number the account actually cares about. The per-provider ceilings stay
      as they were (W1-3c): they move traffic down the chain, this one stops it.

    The addresses are the registry's, per app instance; the day's counts are
    the process ledger's.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        ledger: UsageLedger = LEDGER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self._clock = clock
        self._recent: dict[str, deque[float]] = {}

    def _window(self, address: str) -> deque[float]:
        now = self._clock()
        recent = self._recent.get(address)
        if recent is None:
            recent = self._recent[address] = deque()
        while recent and now - recent[0] >= 3600:
            recent.popleft()
        return recent

    def refusal(self, address: str) -> str | None:
        """``daily_cap`` or ``address_cap`` when a turn may not start, else None."""
        settings = self.settings
        if settings.daily_turn_cap and self.ledger.turns_today() >= settings.daily_turn_cap:
            return "daily_cap"
        if self.ledger.over_total_cap(settings.daily_cap_usd):
            return "daily_cap"
        limit = settings.address_turns_per_hour
        if address and limit and len(self._window(address)) >= limit:
            return "address_cap"
        return None

    def record(self, address: str) -> None:
        """A turn started. Called only once it has."""
        self.ledger.record_turn()
        if address and self.settings.address_turns_per_hour:
            self._window(address).append(self._clock())
        # Idle addresses are dropped as the map grows, so it stays as small as
        # the last hour's visitors.
        if len(self._recent) > 1024:
            for key in [key for key in self._recent if not self._window(key)]:
                del self._recent[key]
