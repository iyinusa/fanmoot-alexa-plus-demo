"""The cost guard: the demo-wide ceilings on turns and spend (W6-3)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.agent.usage import TurnGate, UsageLedger
from app.main import create_app
from app.settings import DEFAULT_PROVIDER_CAPS, Settings
from tests.fake_mcp import RunningServer
from tests.scripted import ScriptedLlm, say
from tests.test_models import _chain, _drain
from tests.test_ws import script, until


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _gate(**overrides: object) -> tuple[TurnGate, UsageLedger, Clock]:
    settings = Settings(
        **{"daily_cap_usd": 0, "daily_turn_cap": 0, "address_turns_per_hour": 0, **overrides}  # type: ignore[arg-type]
    )
    ledger, clock = UsageLedger(), Clock()
    return TurnGate(settings, ledger=ledger, clock=clock), ledger, clock


# ── Settings ─────────────────────────────────────────────────────────────────
def test_a_deploy_that_sets_nothing_is_still_capped() -> None:
    # docker-compose.yml passes an unset variable as "", which must not mean
    # "no ceiling" on a public URL.
    settings = Settings(provider_daily_cap_usd="")

    assert settings.provider_daily_cap_usd == DEFAULT_PROVIDER_CAPS
    assert settings.provider_caps == {
        "bedrock": 30.0,
        "gemini": 10.0,
        "polly": 4.0,
        "transcribe": 4.0,
    }
    assert Settings().daily_cap_usd > 0
    assert Settings().daily_turn_cap > 0
    assert Settings().address_turns_per_hour > 0


def test_the_ceilings_can_be_turned_off_on_purpose() -> None:
    assert Settings(provider_daily_cap_usd="{}").provider_caps == {}


# ── The gate ─────────────────────────────────────────────────────────────────
def test_nothing_set_lets_everything_through() -> None:
    gate, _ledger, _clock = _gate()
    for _ in range(500):
        assert gate.refusal("203.0.113.9") is None
        gate.record("203.0.113.9")


def test_the_daily_turn_ceiling() -> None:
    gate, ledger, _clock = _gate(daily_turn_cap=3)
    for _ in range(3):
        assert gate.refusal("a") is None
        gate.record("a")

    # Every address, not just the one that used them.
    assert gate.refusal("b") == "daily_cap"
    assert ledger.turns_today() == 3


def test_the_daily_spend_ceiling_counts_every_provider() -> None:
    gate, ledger, _clock = _gate(daily_cap_usd=1.0)
    ledger.record("bedrock", 1, 1, 0.6)
    assert gate.refusal("a") is None

    ledger.record("gemini", 1, 1, 0.4)
    assert gate.refusal("a") == "daily_cap"


def test_an_unpriced_call_does_not_count_as_free_spend_but_the_turn_counts() -> None:
    gate, ledger, _clock = _gate(daily_cap_usd=1.0, daily_turn_cap=1)
    ledger.record("mystery", 10_000, 100, None)

    assert ledger.spent_total_today() == 0
    gate.record("a")
    assert gate.refusal("a") == "daily_cap"


def test_one_address_per_hour_and_a_sliding_window() -> None:
    gate, _ledger, clock = _gate(address_turns_per_hour=2)
    for _ in range(2):
        gate.record("198.51.100.7")

    assert gate.refusal("198.51.100.7") == "address_cap"
    assert gate.refusal("198.51.100.8") is None

    clock.now += 3_599
    assert gate.refusal("198.51.100.7") == "address_cap"
    clock.now += 1
    assert gate.refusal("198.51.100.7") is None


def test_a_caller_with_no_address_is_not_limited_per_address() -> None:
    gate, _ledger, _clock = _gate(address_turns_per_hour=1)
    gate.record("")
    gate.record("")
    assert gate.refusal("") is None


def test_the_address_map_forgets_idle_visitors() -> None:
    gate, _ledger, clock = _gate(address_turns_per_hour=5)
    for n in range(1_100):
        gate.record(f"10.0.{n // 256}.{n % 256}")
    clock.now += 3_600
    gate.record("10.9.9.9")

    assert len(gate._recent) == 1


# ── Mid-turn ─────────────────────────────────────────────────────────────────
async def test_a_turn_that_crosses_the_ceiling_stops_at_its_next_model_call() -> None:
    usage = UsageLedger()
    usage.record("gamma", 1, 1, 50.0)
    alpha = ScriptedLlm.of(say("never"))
    llm, ledger, _frames, _usage = _chain({"alpha/one": alpha}, usage=usage)
    llm._settings = llm._settings.model_copy(update={"daily_cap_usd": 40.0})  # type: ignore[union-attr]

    with pytest.raises(Exception, match="No model"):
        await _drain(llm)
    assert alpha.remaining == 1
    assert ledger.skipped == [
        {"model": "alpha/one", "reason": "the demo is over its daily ceiling"}
    ]


# ── Over /ws ─────────────────────────────────────────────────────────────────
@pytest.fixture
def capped(configure: Callable[..., Settings], mcp_server: RunningServer) -> Iterator[TestClient]:
    configure(mcp_url=mcp_server.url, address_turns_per_hour=2)
    with TestClient(create_app()) as test_client:
        yield test_client


def test_a_reload_does_not_reset_the_address_allowance(capped: TestClient) -> None:
    script(capped, ScriptedLlm.of(say("One."), say("Two."), say("Three.")))
    forwarded = {"X-Forwarded-For": "192.0.2.44, 10.0.0.2"}

    with capped.websocket_connect("/ws", headers=forwarded) as first:
        for n in range(2):
            first.send_json({"type": "turn", "text": f"question {n}"})
            until(first, "turn.end")
    # A fresh session — a reload — from the same visitor.
    with capped.websocket_connect("/ws", headers=forwarded) as second:
        second.send_json({"type": "turn", "text": "one more"})
        error = until(second, "error")
    assert error["code"] == "address_cap"

    # Someone else behind the same proxy is unaffected.
    with capped.websocket_connect("/ws", headers={"X-Forwarded-For": "192.0.2.45"}) as other:
        other.send_json({"type": "turn", "text": "hello"})
        assert until(other, "turn.end")["status"] == "ok"


def test_a_refused_turn_is_not_counted(capped: TestClient) -> None:
    script(capped, ScriptedLlm.of(say("One."), say("Two.")))
    forwarded = {"X-Forwarded-For": "192.0.2.50"}
    registry = capped.app.state.registry  # type: ignore[attr-defined]

    with capped.websocket_connect("/ws", headers=forwarded) as socket:
        socket.send_json({"type": "turn", "text": "first"})
        socket.send_json({"type": "turn", "text": "while busy"})
        assert until(socket, "error")["code"] == "busy"
        until(socket, "turn.end")

    assert len(registry.gate._recent["192.0.2.50"]) == 1
