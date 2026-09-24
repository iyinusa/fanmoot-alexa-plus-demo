"""The model chain: per-turn resolution, per-call failover, accounting (W1-3, W1-3b, W1-3c)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from app.agent import models
from app.agent.models import (
    FailoverLlm,
    ModelRung,
    NoModelAvailable,
    TurnLedger,
    is_retryable,
    parse_chain,
)
from app.agent.trace import Tracer
from app.agent.usage import UsageLedger, estimate_usd
from app.settings import DEFAULT_MODEL_CHAIN, Settings
from tests.scripted import ScriptedLlm, fail, say


def test_the_chain_parses_in_order_without_duplicates() -> None:
    chain = parse_chain(
        " bedrock/us.amazon.nova-pro-v1:0, gemini/gemini-2.5-flash,\n"
        "bedrock/us.amazon.nova-pro-v1:0 ,"
    )

    assert [rung.qualified for rung in chain] == [
        "bedrock/us.amazon.nova-pro-v1:0",
        "gemini/gemini-2.5-flash",
    ]
    assert [rung.position for rung in chain] == [0, 1]
    assert chain[0].provider == "bedrock"
    assert chain[0].model_id == "us.amazon.nova-pro-v1:0"


def test_the_default_chain_puts_bedrock_first() -> None:
    # HAD-9: Bedrock first, genuinely exercised, for AWS Builder.
    chain = parse_chain(DEFAULT_MODEL_CHAIN)

    assert chain[0].provider == "bedrock"
    assert "nova" in chain[0].model_id
    assert {rung.provider for rung in chain} == {"bedrock", "gemini"}


def test_an_override_replaces_the_chain_for_the_next_turn() -> None:
    settings = Settings(model_chain="bedrock/a,gemini/b")

    models.set_override(["gemini/b", "bedrock/a"])
    assert [rung.qualified for rung in models.current_chain(settings)] == ["gemini/b", "bedrock/a"]

    models.set_override(None)
    assert [rung.qualified for rung in models.current_chain(settings)] == ["bedrock/a", "gemini/b"]


def test_a_rung_without_its_credentials_is_named_as_such() -> None:
    gemini = ModelRung("gemini/gemini-2.5-flash", 0)
    bedrock = ModelRung("bedrock/us.amazon.nova-pro-v1:0", 0)

    assert (
        models.unconfigured_reason(gemini, Settings(gemini_api_key="")) == "no FMD_GEMINI_API_KEY"
    )
    assert models.unconfigured_reason(gemini, Settings(gemini_api_key="k")) is None
    assert models.unconfigured_reason(bedrock, Settings(aws_access_key_id="AKIA")) is None


def test_credentials_are_passed_to_the_model_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    settings = Settings(
        aws_region="eu-west-2",
        aws_access_key_id="AKIA",
        aws_secret_access_key="s",
        gemini_api_key="g",
    )

    bedrock = models.build_llm(ModelRung("bedrock/us.amazon.nova-pro-v1:0", 0), settings)
    gemini = models.build_llm(ModelRung("gemini/gemini-2.5-flash", 1), settings)

    assert bedrock._additional_args["aws_region_name"] == "eu-west-2"  # type: ignore[attr-defined]
    assert bedrock._additional_args["aws_access_key_id"] == "AKIA"  # type: ignore[attr-defined]
    assert bedrock._additional_args["num_retries"] == 0  # type: ignore[attr-defined]
    # Gemini goes through ADK's native class, with its key on the instance.
    assert gemini.model == "gemini-2.5-flash"
    assert gemini.client_kwargs["api_key"] == "g"  # type: ignore[attr-defined]
    assert "AWS_ACCESS_KEY_ID" not in os.environ


@pytest.mark.parametrize(
    ("message", "retryable"),
    [
        ("litellm.RateLimitError: 429 Too Many Requests", True),
        ("ThrottlingException: Rate exceeded", True),
        ("litellm.Timeout: Request timed out", True),
        ("ServiceUnavailableError: 503", True),
        ("AccessDeniedException: You don't have access to the model", True),
        ("BadRequestError: context length exceeded", False),
        ("ContentPolicyViolationError: content_filter", False),
        ("ValueError: something odd", False),
    ],
)
def test_which_failures_fall_through(message: str, retryable: bool) -> None:
    assert is_retryable(RuntimeError(message)) is retryable


# ── FailoverLlm ──────────────────────────────────────────────────────────────
def _request() -> LlmRequest:
    return LlmRequest(
        model="fanmoot-failover-chain",
        contents=[types.Content(role="user", parts=[types.Part(text="score?")])],
    )


def _chain(
    scripts: dict[str, ScriptedLlm], *, caps: str = "", usage: UsageLedger | None = None
) -> tuple[FailoverLlm, TurnLedger, list[dict[str, Any]], UsageLedger]:
    settings = Settings(model_chain=",".join(scripts), provider_daily_cap_usd=caps)
    ledger = TurnLedger()
    frames: list[dict[str, Any]] = []
    usage = usage or UsageLedger()
    factory: Callable[[ModelRung, Settings], Any] = lambda rung, _s: scripts[rung.qualified]  # noqa: E731
    llm = FailoverLlm.for_turn(
        parse_chain(settings.model_chain),
        settings=settings,
        ledger=ledger,
        tracer=Tracer(sink=frames.append),
        usage=usage,
        factory=factory,
    )
    return llm, ledger, frames, usage


async def _drain(llm: FailoverLlm, stream: bool = True) -> str:
    text = ""
    async for response in llm.generate_content_async(_request(), stream=stream):
        if not response.partial and response.content and response.content.parts:
            text = "".join(part.text or "" for part in response.content.parts)
    return text


async def test_the_first_rung_answers_when_it_can() -> None:
    llm, ledger, frames, usage = _chain(
        {"alpha/one": ScriptedLlm.of(say("Two-one.")), "beta/two": ScriptedLlm.of()}
    )

    assert await _drain(llm) == "Two-one."
    assert ledger.model == "alpha/one"
    assert ledger.failovers == []
    assert usage.snapshot({})["providers"]["alpha"]["calls"] == 1
    assert [frame["method"] for frame in frames] == ["model call"]


async def test_each_adapter_is_asked_for_its_own_model_name() -> None:
    # ADK's native Gemini is built with the bare id and puts the request's
    # model into the URL; LiteLlm is built with the qualified one. Each must be
    # handed the name it was built with, never the rung's `provider/…` id.
    bare = ScriptedLlm.of(say("Two-one."), model="flash-lite")
    qualified = ScriptedLlm.of(say("Two-one."), model="beta/two")
    llm, _ledger, _frames, _usage = _chain({"alpha/flash-lite": bare, "beta/two": qualified})

    await _drain(llm)
    assert [request.model for request in bare.requests] == ["flash-lite"]


async def test_a_throttled_provider_falls_through_to_the_next() -> None:
    llm, ledger, frames, usage = _chain(
        {
            "alpha/one": ScriptedLlm.of(fail(RuntimeError("429 ThrottlingException"))),
            "beta/two": ScriptedLlm.of(say("Two-one.")),
        }
    )

    assert await _drain(llm) == "Two-one."
    assert ledger.model == "beta/two"
    assert ledger.failovers[0]["from"] == "alpha/one"
    assert ledger.failovers[0]["fell_through"] is True
    # On the trace, in order: the failover, then the call that answered.
    assert [frame["method"] for frame in frames] == ["failover", "model call"]
    assert usage.snapshot({})["providers"]["alpha"]["failures"] == 1


async def test_the_turn_stays_below_a_rung_that_failed() -> None:
    beta = ScriptedLlm.of(say("First."), say("Second."))
    alpha = ScriptedLlm.of(fail(RuntimeError("503 service unavailable")), say("never"))
    llm, ledger, _frames, _usage = _chain({"alpha/one": alpha, "beta/two": beta})

    await _drain(llm)
    await _drain(llm)

    # The second call of the turn went straight to beta.
    assert alpha.remaining == 1
    assert [call.model for call in ledger.calls] == ["beta/two", "beta/two"]


async def test_a_request_every_provider_would_refuse_is_not_retried() -> None:
    beta = ScriptedLlm.of(say("never"))
    llm, ledger, _frames, _usage = _chain(
        {
            "alpha/one": ScriptedLlm.of(fail(RuntimeError("context length exceeded"))),
            "beta/two": beta,
        }
    )

    with pytest.raises(RuntimeError, match="context length"):
        await _drain(llm)
    assert beta.remaining == 1
    assert ledger.failovers[0]["fell_through"] is False


async def test_a_failure_after_words_were_streamed_is_not_replayed() -> None:
    beta = ScriptedLlm.of(say("never"))
    llm, _ledger, _frames, _usage = _chain(
        {
            "alpha/one": ScriptedLlm.of(fail(RuntimeError("timeout"), after_words="Arsenal are")),
            "beta/two": beta,
        }
    )

    with pytest.raises(RuntimeError, match="timeout"):
        await _drain(llm)
    # The visitor already heard "Arsenal are"; a second model starting over
    # would say it twice.
    assert beta.remaining == 1


async def test_a_provider_over_its_ceiling_is_skipped() -> None:
    usage = UsageLedger()
    usage.record("alpha", 1000, 100, 5.0)
    alpha = ScriptedLlm.of(say("never"))
    llm, ledger, frames, _usage = _chain(
        {"alpha/one": alpha, "beta/two": ScriptedLlm.of(say("Two-one."))},
        caps='{"alpha": 5}',
        usage=usage,
    )

    assert await _drain(llm) == "Two-one."
    assert alpha.remaining == 1
    assert ledger.skipped == [{"model": "alpha/one", "reason": "alpha is over its daily ceiling"}]
    assert frames[0]["method"] == "skip"


async def test_everything_failing_says_so() -> None:
    llm, _ledger, _frames, _usage = _chain(
        {
            "alpha/one": ScriptedLlm.of(fail(RuntimeError("429"))),
            "beta/two": ScriptedLlm.of(fail(RuntimeError("503"))),
        }
    )

    with pytest.raises(NoModelAvailable):
        await _drain(llm)


async def test_each_call_is_costed_and_recorded() -> None:
    llm, ledger, frames, _usage = _chain({"alpha/one": ScriptedLlm.of(say("Two-one."))})

    await _drain(llm)

    call = ledger.calls[0]
    assert (call.tokens_in, call.tokens_out) == (200, 20)
    assert call.model_version == "scripted-v1"
    # Nobody priced "alpha/one": unknown, never zero.
    assert call.cost_usd is None
    assert ledger.totals()["cost_complete"] is False
    assert frames[0]["tokens_in"] == 200


def test_prices_come_from_litellm_or_the_override() -> None:
    known = estimate_usd("bedrock/us.amazon.nova-pro-v1:0", 1_000_000, 0, {})
    overridden = estimate_usd("custom/model", 1_000_000, 1_000_000, {"custom/model": (1.0, 2.0)})

    assert known is not None and known > 0
    assert overridden == pytest.approx(3.0)
    assert estimate_usd("nobody/knows", 10, 10, {}) is None
