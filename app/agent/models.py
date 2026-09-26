"""The model chain: resolved per turn, failed over per call (HAD-9, W1-3, W1-3b).

``FMD_MODEL_CHAIN`` names LiteLLM model ids, and **the order it gives is the
order they are tried** — nothing here prefers one provider over another. Put
Gemini first and Gemini answers; put Nova first and Nova does. An id written
without its provider (``gemini-3.1-flash-lite``) has the provider inferred from
its name (``qualify``), because LiteLLM would otherwise take a bare id for an
OpenAI model and the rung would fail on every call. Four properties, each
deliberate:

**Read per turn.** ``current_chain()`` is called at the start of every turn,
and ``PUT /api/admin/models`` replaces it in memory, so the chain can be
reordered on a running demo and the next turn uses the new order. No redeploy,
no restart.

**Failover per model call, not per turn.** ``FailoverLlm`` is the model the ADK
agent sees. Each time ADK asks it for a response it tries the chain's rungs in
order, and a rung that fails in any way *before its first words or tool call*
falls through to the next — an exception of any kind, an error response (a
content filter, a malformed tool call, a token limit) or an empty answer. No
error text is matched: providers word the same failure a dozen ways, and a
list of the retryable ones is always missing the one that happens on stage.
Doing it per call rather than per turn is the difference between safe and
unsafe: a turn that already posted a take and failed on the follow-up sentence
must not be replayed from the top on the next provider, or the take goes out
twice. A failure *after* the first words have been streamed to the visitor is
not retried either — the words are already on screen.

**Sticky within a turn.** Once a rung has failed in a turn, the turn's later
calls start below it. Asking a provider that just timed out for the next
sentence of the same answer only doubles the wait.

**Ceilings and evidence.** A provider past its daily ceiling
(``FMD_PROVIDER_DAILY_CAP_USD``) is skipped like a failed one. Every call is
recorded on the turn with the model id that actually answered, its timings, its
tokens and its cost (W1-3c, W1-10), and each call and each failover is a
``model`` frame on the trace, so a failover mid-demo is on screen (W3-4) rather
than a mystery.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr

from app.agent.trace import Tracer
from app.agent.usage import LEDGER, UsageLedger, estimate_usd
from app.logging_config import get_logger
from app.settings import Settings

log = get_logger("agent.models")


class NoModelAvailable(Exception):
    """Every rung is misconfigured, over its ceiling, or failed."""


@dataclass(frozen=True)
class ModelRung:
    """One entry in the chain: a LiteLLM model id and its position."""

    qualified: str
    position: int

    @property
    def provider(self) -> str:
        prefix, _, rest = self.qualified.partition("/")
        return prefix.lower() if rest else "openai"

    @property
    def model_id(self) -> str:
        return self.qualified.partition("/")[2] or self.qualified


#: Bare model ids, by the start of their name, and the provider that serves
#: them. Bedrock's ids carry the vendor (``amazon.``, ``anthropic.``), often
#: behind a cross-region inference prefix (``us.``, ``eu.``, ``apac.``).
_BARE_PROVIDERS = (
    (("gemini-", "gemma-"), "gemini"),
    (
        tuple(
            f"{region}{vendor}."
            for region in ("", "us.", "eu.", "apac.", "global.")
            for vendor in ("amazon", "anthropic", "meta", "mistral", "cohere", "ai21", "deepseek")
        ),
        "bedrock",
    ),
)


def qualify(name: str) -> str:
    """``name`` with its provider prefix, inferred when it was left off.

    ``gemini-3.1-flash-lite`` becomes ``gemini/gemini-3.1-flash-lite`` and
    ``us.amazon.nova-pro-v1:0`` becomes ``bedrock/us.amazon.nova-pro-v1:0``.
    Anything already prefixed, or not recognised, is left exactly as written.
    """
    if "/" in name:
        return name
    lowered = name.lower()
    for prefixes, provider in _BARE_PROVIDERS:
        if lowered.startswith(prefixes):
            return f"{provider}/{name}"
    return name


def parse_chain(raw: str) -> list[ModelRung]:
    names: list[str] = []
    for item in raw.replace("\n", ",").split(","):
        name = qualify(item.strip()) if item.strip() else ""
        if name and name not in names:
            names.append(name)
    return [ModelRung(qualified=name, position=index) for index, name in enumerate(names)]


# ── The chain in force ───────────────────────────────────────────────────────
_override: list[str] | None = None


def current_chain(settings: Settings) -> list[ModelRung]:
    """The chain for the turn about to start: the admin override, or the setting."""
    if _override is not None:
        return parse_chain(",".join(_override))
    return parse_chain(settings.model_chain)


def set_override(chain: list[str] | None) -> None:
    """Replace the chain in memory (``PUT /api/admin/models``), or clear it."""
    global _override
    _override = [name.strip() for name in chain if name.strip()] if chain is not None else None
    log.info("model_chain_override", chain=_override)


def override_active() -> bool:
    return _override is not None


# ── Building a live model ────────────────────────────────────────────────────
def unconfigured_reason(rung: ModelRung, settings: Settings) -> str | None:
    """Why a rung cannot be tried at all, or None when it can.

    Checked before a call rather than discovered by one: a Gemini rung with no
    key would fail the same way every time, and "no credentials" belongs on the
    trace as a skip, not as a failover that looks like an outage.
    """
    if rung.provider == "gemini" and not settings.gemini_api_key.strip():
        return "no FMD_GEMINI_API_KEY"
    if (
        rung.provider == "bedrock"
        and not settings.aws_access_key_id
        and not _aws_chain_has_credentials()
    ):
        return "no AWS credentials"
    return None


_aws_checked: tuple[float, bool] | None = None


def _aws_chain_has_credentials() -> bool:
    """Whether boto3's default chain finds credentials. Cached for a minute."""
    global _aws_checked
    now = time.time()
    if _aws_checked is not None and now - _aws_checked[0] < 60:
        return _aws_checked[1]
    try:
        import boto3  # noqa: PLC0415

        found = boto3.Session().get_credentials() is not None
    except Exception:
        found = False
    _aws_checked = (now, found)
    return found


def preload(settings: Settings) -> None:
    """Import the model adapters and LiteLLM's price table before anyone asks (W6-2).

    They are imported lazily, at the first turn, and on a cold process that is
    over a second of a judge's first question spent importing, on top of the
    provider's own first TLS handshake. Run once at startup in a thread, so it
    holds up neither the boot nor the event loop. Failures are ignored: the
    first turn simply imports them itself, as before.
    """
    started = time.perf_counter()
    try:
        rungs = parse_chain(settings.model_chain)
        # Building a Gemini adapter makes no request; it imports the SDK.
        for rung in rungs:
            if rung.provider == "gemini":
                build_llm(rung, settings)
                break
        if any(rung.provider != "gemini" for rung in rungs):
            from google.adk.models.lite_llm import LiteLlm  # noqa: F401, PLC0415

        from app.agent.usage import estimate_usd  # noqa: PLC0415

        estimate_usd("bedrock/us.amazon.nova-pro-v1:0", 1, 1, {})
    except Exception as exc:
        log.warning("model_preload_failed", error=str(exc)[:200])
        return
    log.info("model_preloaded", ms=round((time.perf_counter() - started) * 1000))


def build_llm(rung: ModelRung, settings: Settings) -> BaseLlm:
    """A live ADK model for one rung, with its credentials passed to it directly.

    Never through ``os.environ``: two visitors' turns run concurrently, and a
    process-global key would be whichever turn wrote it last (the same care
    ``fanmoot``'s resolver takes).

    Bedrock, and anything else, goes through ADK's ``LiteLlm``. **Gemini goes
    through ADK's native ``Gemini`` class** instead — ADK's own recommendation,
    and ``fanmoot``'s resolver does the same. Gemini is the rung that has to
    work when Bedrock does not, so it gets the better-supported path. The
    rung's id stays ``gemini/…`` either way, which is what is priced, recorded
    and shown.
    """
    if rung.provider == "gemini":
        from google.adk.models.google_llm import Gemini  # noqa: PLC0415
        from google.genai import types as genai  # noqa: PLC0415

        return Gemini(
            model=rung.model_id,
            client_kwargs={
                "api_key": settings.gemini_api_key,
                # AI Studio, never Vertex: there is no service account here.
                "vertexai": False,
                "http_options": genai.HttpOptions(timeout=int(settings.model_timeout_s * 1000)),
            },
        )

    from google.adk.models.lite_llm import LiteLlm  # noqa: PLC0415

    kwargs: dict[str, Any] = {
        "timeout": settings.model_timeout_s,
        # Failover is this module's job. LiteLLM retrying underneath it would
        # turn one slow provider into three slow attempts before the next rung.
        "num_retries": 0,
    }
    if rung.provider == "bedrock":
        kwargs["aws_region_name"] = settings.aws_region
        if settings.aws_access_key_id:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
            if settings.aws_session_token:
                kwargs["aws_session_token"] = settings.aws_session_token
    return LiteLlm(model=rung.qualified, **kwargs)


# ── What counts as an answer ─────────────────────────────────────────────────
class ModelFailed(Exception):
    """A rung that answered without an exception, but with nothing usable."""


def _has_output(response: LlmResponse) -> bool:
    """Whether a response carries anything the visitor hears or ADK acts on.

    Text (not thoughts) or a tool call. Empty chunks, thought chunks and bare
    usage chunks do not count: none of them reach the visitor, so a rung that
    has sent only those can still be failed over from without saying anything
    twice.
    """
    parts = response.content.parts if response.content and response.content.parts else []
    return any((part.text and not part.thought) or part.function_call for part in parts)


# ── The turn's ledger ────────────────────────────────────────────────────────
@dataclass
class ModelCall:
    model: str
    provider: str
    position: int
    ms: float
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    model_version: str | None


@dataclass
class TurnLedger:
    """Every model call and failover in one turn — what `turn.end` reports."""

    calls: list[ModelCall] = field(default_factory=list)
    failovers: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    #: The rung the next call starts at: below any that failed this turn.
    start: int = 0

    @property
    def model(self) -> str | None:
        """The model that answered last — the one the visitor heard."""
        return self.calls[-1].model if self.calls else None

    @property
    def provider(self) -> str | None:
        return self.calls[-1].provider if self.calls else None

    def totals(self) -> dict[str, Any]:
        priced = [call.cost_usd for call in self.calls if call.cost_usd is not None]
        return {
            "calls": len(self.calls),
            "tokens_in": sum(call.tokens_in for call in self.calls),
            "tokens_out": sum(call.tokens_out for call in self.calls),
            "cost_usd": round(sum(priced), 6) if priced else None,
            "cost_complete": len(priced) == len(self.calls),
        }

    def public(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "models": sorted({call.model for call in self.calls}),
            "calls": [call.__dict__ for call in self.calls],
            "failovers": self.failovers,
            "skipped": self.skipped,
            "usage": self.totals(),
        }


LlmFactory = Callable[[ModelRung, Settings], BaseLlm]


class FailoverLlm(BaseLlm):
    """The model ADK sees: the chain, tried in order, one call at a time."""

    model: str = "fanmoot-failover-chain"

    _rungs: list[ModelRung] = PrivateAttr(default_factory=list)
    _built: dict[int, BaseLlm] = PrivateAttr(default_factory=dict)
    _ledger: TurnLedger = PrivateAttr(default_factory=TurnLedger)
    _settings: Settings | None = PrivateAttr(default=None)
    _tracer: Tracer | None = PrivateAttr(default=None)
    _usage: UsageLedger = PrivateAttr(default=LEDGER)
    # None means `build_llm`. A function as a private attribute's default is
    # bound like a method by pydantic, so the default is resolved at call time.
    _factory: LlmFactory | None = PrivateAttr(default=None)

    @classmethod
    def for_turn(
        cls,
        rungs: list[ModelRung],
        *,
        settings: Settings,
        ledger: TurnLedger,
        tracer: Tracer,
        usage: UsageLedger = LEDGER,
        factory: LlmFactory = build_llm,
    ) -> FailoverLlm:
        llm = cls()
        llm._rungs = rungs
        llm._settings = settings
        llm._ledger = ledger
        llm._tracer = tracer
        llm._usage = usage
        llm._factory = factory
        return llm

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        settings, tracer = self._bound()
        ledger = self._ledger
        caps = settings.provider_caps
        last_error: BaseException | None = None

        for rung in self._rungs[ledger.start :]:
            reason = unconfigured_reason(rung, settings)
            if reason is None and self._usage.over_cap(rung.provider, caps):
                reason = f"{rung.provider} is over its daily ceiling"
            # The demo-wide ceiling is checked per call as well as per turn
            # (W6-3), so a turn that crosses it stops at its next model call
            # instead of running to the end.
            if reason is None and self._usage.over_total_cap(settings.daily_cap_usd):
                reason = "the demo is over its daily ceiling"
            if reason is not None:
                self._skip(rung, reason)
                continue

            llm = self._built.get(rung.position) or (self._factory or build_llm)(rung, settings)
            self._built[rung.position] = llm
            # A copy per attempt: a provider adapter may append to the contents
            # or add headers to the config, and the next rung must see the
            # request ADK built, not that. Not a deep copy of the whole request:
            # its `tools_dict` holds the live tool bridges, and those hold the
            # visitor's MCP session.
            #
            # The model name is the adapter's own, not the rung's qualified id:
            # LiteLlm is built with ``bedrock/…`` and wants it, but ADK's native
            # Gemini is built with the bare id and puts whatever it is handed
            # into the URL — ``models/gemini/gemini-…`` is a 404 on every call.
            request = llm_request.model_copy(
                update={
                    "model": llm.model or rung.model_id,
                    "contents": list(llm_request.contents),
                    "config": llm_request.config.model_copy(deep=True),
                }
            )
            started = time.perf_counter()
            # The point of no return: the first text or tool call handed on.
            # Until then nothing has reached the visitor, so any failure at
            # all — an exception, an error response, an empty answer — falls
            # through to the next rung. After it, the words are on screen.
            spoke = False
            try:
                async for response in llm.generate_content_async(request, stream=stream):
                    if not spoke:
                        # Streaming reports a filter, a malformed tool call or
                        # a token limit on the closing response, after empty
                        # or thought chunks, not as an exception.
                        if response.error_code:
                            raise ModelFailed(
                                f"{response.error_code}: {response.error_message or ''}".strip()
                            )
                        if not response.partial and not _has_output(response):
                            raise ModelFailed("the model returned an empty response")
                    spoke = spoke or _has_output(response)
                    if not response.partial:
                        self._record(rung, response, started)
                    yield response
                if not spoke:
                    raise ModelFailed("the model returned nothing")
            except Exception as exc:
                self._usage.record_failure(rung.provider)
                self._failed(rung, exc, streamed=spoke, started=started)
                if spoke:
                    raise
                last_error = exc
                ledger.start = rung.position + 1
                continue
            ledger.start = rung.position
            return

        raise NoModelAvailable(
            "No model in the chain could answer."
            + (
                f" Last error: {type(last_error).__name__}: {last_error}"[:300]
                if last_error
                else ""
            )
        )

    # ── Accounting ───────────────────────────────────────────────────────────
    def _bound(self) -> tuple[Settings, Tracer]:
        if self._settings is None or self._tracer is None:
            raise RuntimeError("FailoverLlm is built with FailoverLlm.for_turn().")
        return self._settings, self._tracer

    def _record(self, rung: ModelRung, response: LlmResponse, started: float) -> None:
        settings, tracer = self._bound()
        usage = response.usage_metadata
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0)
        cost = estimate_usd(rung.qualified, tokens_in, tokens_out, settings.model_price_overrides)
        ms = (time.perf_counter() - started) * 1000
        call = ModelCall(
            model=rung.qualified,
            provider=rung.provider,
            position=rung.position,
            ms=round(ms, 1),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=None if cost is None else round(cost, 6),
            model_version=response.model_version,
        )
        self._ledger.calls.append(call)
        self._usage.record(rung.provider, tokens_in, tokens_out, cost)
        tracer.emit(
            "model",
            direction="in",
            method="model call",
            ms=ms,
            summary=f"{rung.qualified} · {tokens_in}→{tokens_out} tokens"
            + (f" · ${cost:.5f}" if cost is not None else " · price unknown"),
            model=rung.qualified,
            provider=rung.provider,
            position=rung.position,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=call.cost_usd,
            model_version=response.model_version,
        )
        log.info(
            "model_call",
            model=rung.qualified,
            provider=rung.provider,
            position=rung.position,
            ms=call.ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=call.cost_usd,
        )

    def _skip(self, rung: ModelRung, reason: str) -> None:
        _, tracer = self._bound()
        entry = {"model": rung.qualified, "reason": reason}
        if entry not in self._ledger.skipped:
            self._ledger.skipped.append(entry)
            tracer.emit(
                "model",
                direction="local",
                method="skip",
                status="error",
                summary=f"{rung.qualified} skipped — {reason}",
                model=rung.qualified,
                provider=rung.provider,
            )

    def _failed(self, rung: ModelRung, exc: Exception, *, streamed: bool, started: float) -> None:
        _, tracer = self._bound()
        error = f"{type(exc).__name__}: {exc}"[:300]
        falls_through = not streamed and rung.position + 1 < len(self._rungs)
        self._ledger.failovers.append(
            {
                "from": rung.qualified,
                "error": error,
                # Kept for the page's protocol: a failure is retryable exactly
                # when the visitor has heard nothing from that rung yet.
                "retryable": not streamed,
                "fell_through": falls_through,
            }
        )
        tracer.emit(
            "model",
            direction="in",
            method="failover" if falls_through else "model error",
            status="error",
            ms=(time.perf_counter() - started) * 1000,
            summary=(
                f"{rung.qualified} failed — trying the next model" if falls_through else error
            ),
            model=rung.qualified,
            provider=rung.provider,
            error=error,
        )
        log.warning(
            "model_call_failed",
            model=rung.qualified,
            fell_through=falls_through,
            streamed=streamed,
            error=error,
        )
