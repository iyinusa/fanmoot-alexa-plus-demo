"""Alexa's ears: the fan's voice, streamed to Amazon Transcribe and back.

**Why here and not in the browser.** The Web Speech API's recogniser is a
front for Google's or Apple's service, and only Chrome, Edge and Safari have
one; Firefox has none, and Brave and editor-embedded browsers have the API but
no service behind it. Every one of them can record the microphone. So the
browser records — 16 kHz, 16-bit PCM, from an AudioWorklet — and sends it up
the ``/ws`` it already has, and this module relays it to Transcribe's
streaming API, which answers with the words as they are heard.

**One ``Listening`` per utterance.** The browser opens one when the mic opens
(``listen.start``), sends binary frames of audio, and closes it when it hears
the fan stop (``listen.stop``). Words heard so far go back as
``listen.partial``; the finished sentence as ``listen.final``. The browser
decides when the fan has stopped — it has the audio's level in hand, sixty
times a second — and this side only relays, and cuts off anything longer than
``FMD_LISTEN_MAX_UTTERANCE_S``.

**The stream is signed here.** Transcribe's WebSocket is opened with a URL
signed with AWS Signature Version 4 (``presign``), from the same credentials
Bedrock uses. The signed URL never leaves this process: the browser only ever
talks to ``/ws``.

**Counted.** Transcribe bills by the second with a 15-second minimum per
stream; each utterance is priced into the ledger under ``transcribe``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from app.agent.usage import LEDGER, UsageLedger
from app.logging_config import get_logger
from app.settings import Settings
from app.voice import eventstream
from app.voice.aws import AwsKeys, Outage, VoiceUnavailable, aws_keys

log = get_logger("voice.listen")

#: Transcribe streaming, standard, in US dollars per second, and its minimum.
PRICE_PER_SECOND = 0.024 / 60
MINIMUM_SECONDS = 15.0

#: The sample rates the browser may send. 16 kHz is what it is asked for.
RATES = range(8_000, 48_001)

#: How long Transcribe has to send its last words after the audio ends.
_FINAL_WAIT_S = 6.0
#: Audio chunks held while the stream to Transcribe is still opening.
_QUEUE_LIMIT = 600
#: Exceptions in the stream that mean the account, not the audio: Transcribe
#: authenticates the signed URL, opens the socket, and only then says no.
_DENIED = {"AccessDeniedException", "UnrecognizedClientException", "ExpiredTokenException"}

Send = Callable[[dict[str, Any]], None]


def presign(
    keys: AwsKeys,
    *,
    region: str,
    language: str,
    rate: int,
    now: datetime | None = None,
    expires_s: int = 300,
) -> str:
    """Transcribe's WebSocket URL, signed with SigV4 in the query string.

    Exactly the algorithm in Transcribe's "Setting up a WebSocket stream":
    service ``transcribe``, host with its port, ``host`` as the one signed
    header, and the SHA-256 of an empty payload.
    """
    now = now or datetime.now(UTC)
    host = f"transcribestreaming.{region}.amazonaws.com:8443"
    path = "/stream-transcription-websocket"
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{day}/{region}/transcribe/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{keys.access_key}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(expires_s),
        "X-Amz-SignedHeaders": "host",
        "language-code": language,
        "media-encoding": "pcm",
        "sample-rate": str(rate),
        # Words stop changing sooner, so what the fan sees settles as they speak.
        "enable-partial-results-stabilization": "true",
        "partial-results-stability": "medium",
    }
    if keys.session_token:
        params["X-Amz-Security-Token"] = keys.session_token
    query = "&".join(
        f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
        for name, value in sorted(params.items())
    )
    canonical = "\n".join(
        ["GET", path, query, f"host:{host}\n", "host", hashlib.sha256(b"").hexdigest()]
    )
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )

    def mac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    key = f"AWS4{keys.secret_key}".encode()
    for part in (day, region, "transcribe", "aws4_request"):
        key = mac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"wss://{host}{path}?{query}&X-Amz-Signature={signature}"


_TLS: ssl.SSLContext | None = None


def _tls() -> ssl.SSLContext:
    """Mozilla's CA bundle, as httpx uses: the same trust on a laptop as in Docker."""
    global _TLS
    if _TLS is None:
        import certifi  # noqa: PLC0415

        _TLS = ssl.create_default_context(cafile=certifi.where())
    return _TLS


async def _open(url: str) -> Any:
    from websockets.asyncio.client import connect  # noqa: PLC0415

    return await connect(url, ssl=_tls(), open_timeout=6, ping_interval=None, max_size=1 << 20)


class Listening:
    """One utterance, relayed: audio in from ``feed``, words out to ``send``."""

    def __init__(
        self,
        url: str,
        *,
        listen_id: int,
        rate: int,
        send: Send,
        outage: Outage,
        max_seconds: float,
        ledger: UsageLedger = LEDGER,
        opener: Callable[[str], Any] = _open,
    ) -> None:
        self.url = url
        self.id = listen_id
        self.rate = rate
        self._send = send
        self._outage = outage
        self._ledger = ledger
        self._opener = opener
        self._max_bytes = int(max_seconds * rate * 2)
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_QUEUE_LIMIT)
        self._results: dict[str, str] = {}
        self._said = ""
        self.bytes = 0
        self.finished = False
        self.error: VoiceUnavailable | None = None
        self.task = asyncio.create_task(self._run(), name=f"listen-{listen_id}")

    # ── From the browser ─────────────────────────────────────────────────────
    def feed(self, chunk: bytes) -> None:
        if self.finished or not chunk:
            return
        if self.bytes + len(chunk) > self._max_bytes:
            # Longer than anyone asks a question: answer what was heard.
            self.bytes = self._max_bytes
            self._end()
            return
        self.bytes += len(chunk)
        with contextlib.suppress(asyncio.QueueFull):
            self._audio.put_nowait(chunk)

    async def finish(self) -> None:
        """The fan has stopped: flush, wait for the last words, say them."""
        self._end()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(self.task), _FINAL_WAIT_S + 8)

    async def cancel(self) -> None:
        self.finished = True
        self.task.cancel()
        with contextlib.suppress(BaseException):
            await self.task

    @property
    def seconds(self) -> float:
        return self.bytes / (self.rate * 2)

    @property
    def text(self) -> str:
        return " ".join(words for words in self._results.values() if words).strip()

    def _end(self) -> None:
        if self.finished:
            return
        self.finished = True
        while True:
            try:
                self._audio.put_nowait(None)
                return
            except asyncio.QueueFull:
                # A queue full of audio nobody will hear in time: drop the
                # oldest rather than lose the end of the stream.
                self._audio.get_nowait()

    # ── To and from Transcribe ───────────────────────────────────────────────
    async def _run(self) -> None:
        opened = False
        try:
            try:
                socket = await self._opener(self.url)
            except Exception as exc:
                raise self._refused(exc) from exc
            opened = True
            async with socket:
                reader = asyncio.create_task(self._read(socket))
                try:
                    while True:
                        # Audio to send, or Transcribe finishing (or refusing)
                        # first — whichever comes, even mid-pause.
                        getter = asyncio.ensure_future(self._audio.get())
                        done, _ = await asyncio.wait(
                            {getter, reader}, return_when=asyncio.FIRST_COMPLETED
                        )
                        if reader in done:
                            getter.cancel()
                            break
                        chunk = getter.result()
                        await socket.send(eventstream.audio_event(chunk or b""))
                        if chunk is None:
                            break
                    await asyncio.wait_for(asyncio.shield(reader), _FINAL_WAIT_S)
                finally:
                    if not reader.done():
                        reader.cancel()
                    with contextlib.suppress(BaseException):
                        await reader
            self._outage.clear()
            self._send(self._frame("listen.final", text=self.text))
        except VoiceUnavailable as exc:
            self.error = exc
            self._send(self._frame("listen.error", code=exc.code, message=exc.message))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("listen_failed", error=str(exc)[:200])
            # Whatever was heard before it broke is still the fan's question.
            if self.text:
                self._send(self._frame("listen.final", text=self.text))
            else:
                self._send(
                    self._frame(
                        "listen.error", code="failed", message="I couldn't hear that properly."
                    )
                )
        finally:
            self.finished = True
            if opened:
                billed = max(MINIMUM_SECONDS, self.seconds)
                self._ledger.record("transcribe", round(self.seconds), 0, billed * PRICE_PER_SECOND)

    async def _read(self, socket: Any) -> None:
        decoder = eventstream.Decoder()
        async for data in socket:
            if isinstance(data, str):
                continue
            for message in decoder.feed(data):
                kind = message.headers.get(":message-type")
                if kind == "exception":
                    if message.headers.get(":exception-type") in _DENIED:
                        # Signed correctly, and refused anyway: the IAM policy.
                        self._outage.trip(str(message.headers[":exception-type"]))
                        raise VoiceUnavailable("unavailable", "Transcribe refused this account.")
                    raise RuntimeError(_exception_text(message))
                if message.headers.get(":event-type") != "TranscriptEvent":
                    continue
                self._heard(json.loads(message.payload or b"{}"))

    def _heard(self, event: dict[str, Any]) -> None:
        for result in event.get("Transcript", {}).get("Results", []) or []:
            alternatives = result.get("Alternatives") or [{}]
            words = str(alternatives[0].get("Transcript") or "").strip()
            self._results[str(result.get("ResultId") or len(self._results))] = words
        if self.text != self._said:
            self._said = self.text
            self._send(self._frame("listen.partial", text=self._said))

    def _refused(self, exc: Exception) -> VoiceUnavailable:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            # Not a network blip: the IAM policy, the keys or the region.
            self._outage.trip(f"HTTP {status}")
            return VoiceUnavailable("unavailable", "Transcribe refused this account.")
        log.warning("listen_open_failed", error=str(exc)[:200], status=status)
        return VoiceUnavailable("failed", "Transcribe could not be reached just now.")

    def _frame(self, kind: str, **fields: Any) -> dict[str, Any]:
        return {"type": kind, "id": self.id, **fields}


def _exception_text(message: eventstream.Message) -> str:
    kind = message.headers.get(":exception-type", "exception")
    try:
        detail = json.loads(message.payload).get("Message", "")
    except Exception:
        detail = message.payload[:200].decode(errors="replace")
    return f"{kind}: {detail}"


class Ears:
    """Transcribe, for the whole process: whether to offer it, and how to open it."""

    def __init__(
        self,
        settings: Settings,
        *,
        ledger: UsageLedger = LEDGER,
        opener: Callable[[str], Any] = _open,
        keys: Callable[[Settings], AwsKeys | None] = aws_keys,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.outage = Outage("transcribe")
        self._opener = opener
        self._keys = keys

    def refusal(self) -> str | None:
        settings = self.settings
        if not settings.voice_enabled:
            return "off"
        if self.outage.down:
            return "unavailable"
        if self.ledger.over_cap("transcribe", settings.provider_caps) or self.ledger.over_total_cap(
            settings.daily_cap_usd
        ):
            return "daily_cap"
        if self._keys(settings) is None:
            return "no_credentials"
        return None

    @property
    def ready(self) -> bool:
        return self.refusal() is None

    def open(self, *, listen_id: int, rate: int, send: Send) -> Listening:
        """Start relaying one utterance, or ``VoiceUnavailable``."""
        refusal = self.refusal()
        keys = self._keys(self.settings)
        if refusal is not None or keys is None:
            raise VoiceUnavailable(refusal or "no_credentials", "Transcribe is not available.")
        url = presign(
            keys,
            region=self.settings.aws_region,
            language=self.settings.voice_language,
            rate=rate,
        )
        return Listening(
            url,
            listen_id=listen_id,
            rate=rate,
            send=send,
            outage=self.outage,
            max_seconds=self.settings.listen_max_utterance_s,
            ledger=self.ledger,
            opener=self._opener,
        )
