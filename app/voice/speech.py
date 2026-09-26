"""Alexa's voice: one sentence at a time, from Amazon Polly.

The browser asks for each sentence as the answer streams in, so Alexa starts
talking while the rest is still being written — exactly as the browser's own
synthesiser did, with a voice that does not sound like one.

**Generative first, neural behind it.** Polly's generative engine is its most
human voice. A region or voice without it refuses the engine, and the speaker
drops to the neural engine for the rest of the process instead of failing.

**Cached.** The apologies, the tour's lines and "I didn't catch that" are said
far more than once, and a sentence already paid for is not paid for again. The
cache is small and in memory, like everything else here.

**Counted and capped.** Every character is priced into the ledger under
``polly``, so it counts against ``FMD_PROVIDER_DAILY_CAP_USD`` and the
demo-wide ``FMD_DAILY_CAP_USD`` like any model call; a session has its own
allowance too (``FMD_VOICE_CHARS_PER_SESSION``).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from app.agent.usage import LEDGER, UsageLedger
from app.logging_config import get_logger
from app.settings import Settings
from app.voice.aws import Outage, VoiceUnavailable, aws_keys, boto_kwargs

log = get_logger("voice.speech")

#: US dollars per character, from Polly's price list.
PRICE_PER_CHAR = {"generative": 30 / 1_000_000, "neural": 16 / 1_000_000, "standard": 4 / 1_000_000}

#: The longest sentence the browser may ask for. An answer's sentence is well
#: under this; anything longer is not a sentence Alexa said.
MAX_CHARS = 600

#: Sentences kept, and the bytes they may hold between them.
_CACHE_ENTRIES = 256
_CACHE_BYTES = 12 * 1024 * 1024

#: Errors that mean "not with this engine", not "not at all".
_ENGINE_REFUSED = {"EngineNotSupportedException", "ValidationException"}
#: Errors that mean the account, the policy or the keys are wrong.
_DENIED = {
    "AccessDeniedException",
    "UnrecognizedClientException",
    "InvalidSignatureException",
    "ExpiredTokenException",
}


class Speaker:
    """Polly, for the whole process: one client, one cache, one engine choice."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], Any] | None = None,
        ledger: UsageLedger = LEDGER,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.engine = settings.voice_engine.strip().lower() or "generative"
        self.outage = Outage("polly")
        self._client_factory = client_factory or self._boto_client
        self._client: Any = None
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_bytes = 0

    # ── Whether to offer it at all ───────────────────────────────────────────
    def refusal(self) -> str | None:
        """Why the browser should use its own voice, or None to use Polly."""
        settings = self.settings
        if not settings.voice_enabled:
            return "off"
        if self.outage.down:
            return "unavailable"
        if self.ledger.over_cap("polly", settings.provider_caps) or self.ledger.over_total_cap(
            settings.daily_cap_usd
        ):
            return "daily_cap"
        if self._client_factory == self._boto_client and aws_keys(settings) is None:
            return "no_credentials"
        return None

    @property
    def ready(self) -> bool:
        return self.refusal() is None

    def describe(self) -> dict[str, Any]:
        """For the session frame: whether to use it, and whose voice it is."""
        return {"speak": self.ready, "voice": self.settings.voice_id, "engine": self.engine}

    # ── Speaking ─────────────────────────────────────────────────────────────
    async def speak(self, text: str) -> bytes:
        """The sentence as MP3, or ``VoiceUnavailable``."""
        text = " ".join(text.split())
        if not text:
            raise VoiceUnavailable("empty", "Nothing to say.")
        if len(text) > MAX_CHARS:
            raise VoiceUnavailable("too_long", "That is longer than one sentence.")
        key = self._key(text)
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        refusal = self.refusal()
        if refusal is not None:
            raise VoiceUnavailable(refusal, "Polly is not available just now.")

        audio = await asyncio.to_thread(self._synthesise, text)
        self._remember(key, audio)
        return audio

    def _key(self, text: str) -> str:
        # The engine is not in the key: a fallback to neural should not make
        # every cached generative line be bought again, and vice versa.
        return hashlib.sha256(f"{self.settings.voice_id}\n{text}".encode()).hexdigest()

    def _synthesise(self, text: str) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError  # noqa: PLC0415

        for _ in range(2):
            engine = self.engine
            try:
                response = self._client_or_new().synthesize_speech(
                    Text=text,
                    VoiceId=self.settings.voice_id,
                    Engine=engine,
                    OutputFormat="mp3",
                    SampleRate="24000",
                    TextType="text",
                )
                audio: bytes = response["AudioStream"].read()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in _ENGINE_REFUSED and engine != "neural":
                    log.warning("voice_engine_fallback", refused=engine, error=str(exc)[:200])
                    self.engine = "neural"
                    continue
                if code in _DENIED:
                    self.outage.trip(code)
                    raise VoiceUnavailable("unavailable", "Polly refused this account.") from exc
                log.warning("voice_speak_failed", code=code, error=str(exc)[:200])
                raise VoiceUnavailable("failed", "Polly could not say that just now.") from exc
            except BotoCoreError as exc:
                # No credentials, no network, a bad region: not a sentence problem.
                self.outage.trip(type(exc).__name__)
                raise VoiceUnavailable("unavailable", "Polly could not be reached.") from exc
            self.outage.clear()
            characters = int(response.get("RequestCharacters") or len(text))
            cost = characters * PRICE_PER_CHAR.get(engine, PRICE_PER_CHAR["neural"])
            self.ledger.record("polly", characters, 0, cost)
            return audio
        raise VoiceUnavailable("failed", "Polly has no engine for this voice.")

    def _client_or_new(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _boto_client(self) -> Any:
        import boto3  # noqa: PLC0415

        return boto3.client("polly", **boto_kwargs(self.settings))

    def _remember(self, key: str, audio: bytes) -> None:
        if len(audio) > _CACHE_BYTES // 8:
            return
        self._cache[key] = audio
        self._cache_bytes += len(audio)
        while len(self._cache) > _CACHE_ENTRIES or self._cache_bytes > _CACHE_BYTES:
            _, dropped = self._cache.popitem(last=False)
            self._cache_bytes -= len(dropped)
