"""Alexa's voice and ears (``app.voice``): Polly out, Transcribe in.

Offline, like the rest of the suite. Polly is a fake boto3 client; Transcribe
is a real WebSocket server on a loopback port that speaks the same event-stream
framing, so the relay, the codec and the frames the browser gets all run for
real. The request signing is held to botocore's own implementation.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest import mock
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from app.agent.usage import UsageLedger
from app.main import create_app
from app.settings import Settings
from app.voice import eventstream
from app.voice.aws import AwsKeys, VoiceUnavailable
from app.voice.listen import Ears, presign
from app.voice.speech import Speaker
from tests.test_ws import until

KEYS = AwsKeys("AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", None)


# ── The event-stream codec ───────────────────────────────────────────────────
def test_an_audio_event_is_what_botocore_reads() -> None:
    from botocore.eventstream import EventStreamBuffer

    buffer = EventStreamBuffer()
    buffer.add_data(eventstream.audio_event(b"\x01\x02\x03"))
    (message,) = list(buffer)

    assert message.headers[":event-type"] == "AudioEvent"
    assert message.headers[":message-type"] == "event"
    assert message.payload == b"\x01\x02\x03"


def test_messages_split_anywhere_are_put_back_together() -> None:
    stream = eventstream.encode({":event-type": "A"}, b"one") + eventstream.encode(
        {":event-type": "B"}, b"two"
    )
    decoder = eventstream.Decoder()

    heard = [message for byte in stream for message in decoder.feed(bytes([byte]))]

    assert [(m.headers[":event-type"], m.payload) for m in heard] == [("A", b"one"), ("B", b"two")]


def test_a_corrupt_message_is_refused() -> None:
    damaged = bytearray(eventstream.audio_event(b"abc"))
    damaged[-6] ^= 0xFF

    with pytest.raises(eventstream.FrameError):
        eventstream.Decoder().feed(bytes(damaged))


# ── Signing Transcribe's URL ─────────────────────────────────────────────────
@pytest.mark.parametrize("token", [None, "a/session+token=="])
def test_the_signed_url_matches_botocore(token: str | None) -> None:
    from botocore.auth import SigV4QueryAuth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    now = datetime(2026, 9, 26, 8, 0, 0, tzinfo=UTC)
    keys = AwsKeys(KEYS.access_key, KEYS.secret_key, token)
    ours = presign(keys, region="us-east-1", language="en-GB", rate=16_000, now=now)

    query = {
        "language-code": "en-GB",
        "media-encoding": "pcm",
        "sample-rate": "16000",
        "enable-partial-results-stabilization": "true",
        "partial-results-stability": "medium",
    }
    request = AWSRequest(
        method="GET",
        url="https://transcribestreaming.us-east-1.amazonaws.com:8443"
        f"/stream-transcription-websocket?{urlencode(query)}",
    )
    auth = SigV4QueryAuth(
        Credentials(keys.access_key, keys.secret_key, token), "transcribe", "us-east-1", 300
    )
    with mock.patch("botocore.auth.get_current_datetime", return_value=now.replace(tzinfo=None)):
        auth.add_auth(request)

    assert ours.startswith("wss://transcribestreaming.us-east-1.amazonaws.com:8443/")
    assert dict(parse_qsl(urlsplit(ours).query)) == dict(parse_qsl(urlsplit(request.url).query))


# ── Polly ────────────────────────────────────────────────────────────────────
class FakePolly:
    def __init__(self, *, refuse: dict[str, str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.refuse = refuse or {}

    def synthesize_speech(self, **request: Any) -> dict[str, Any]:
        self.calls.append(request)
        code = self.refuse.get(request["Engine"])
        if code:
            raise ClientError({"Error": {"Code": code, "Message": "no"}}, "SynthesizeSpeech")
        audio = f"mp3:{request['Engine']}:{request['Text']}".encode()
        return {"AudioStream": io.BytesIO(audio), "RequestCharacters": len(request["Text"])}


def _speaker(polly: FakePolly, **values: Any) -> tuple[Speaker, UsageLedger]:
    ledger = UsageLedger()
    settings = Settings(voice_enabled=True, **values)
    return Speaker(settings, client_factory=lambda: polly, ledger=ledger), ledger


async def test_polly_speaks_with_the_generative_voice_and_prices_it() -> None:
    polly = FakePolly()
    speaker, ledger = _speaker(polly)

    audio = await speaker.speak("Arsenal are two-one up.")

    assert audio == b"mp3:generative:Arsenal are two-one up."
    assert polly.calls[0]["VoiceId"] == "Amy"
    assert polly.calls[0]["OutputFormat"] == "mp3"
    assert ledger.spent_today("polly") == pytest.approx(len("Arsenal are two-one up.") * 30e-6)


async def test_a_sentence_said_before_is_not_bought_again() -> None:
    polly = FakePolly()
    speaker, _ = _speaker(polly)

    first = await speaker.speak("Sorry, I didn't catch that.")
    again = await speaker.speak("Sorry,  I didn't catch that. ")

    assert first == again
    assert len(polly.calls) == 1


async def test_a_voice_without_the_generative_engine_falls_back_to_neural() -> None:
    polly = FakePolly(refuse={"generative": "ValidationException"})
    speaker, _ = _speaker(polly)

    audio = await speaker.speak("Kick-off is at half seven.")

    assert audio.startswith(b"mp3:neural:")
    assert speaker.engine == "neural"
    assert speaker.describe()["engine"] == "neural"


async def test_a_refused_account_stops_asking_and_says_so() -> None:
    polly = FakePolly(refuse={"generative": "AccessDeniedException"})
    speaker, _ = _speaker(polly)

    with pytest.raises(VoiceUnavailable) as refused:
        await speaker.speak("Hello.")

    assert refused.value.code == "unavailable"
    assert speaker.refusal() == "unavailable"
    assert speaker.describe()["speak"] is False
    with pytest.raises(VoiceUnavailable):
        await speaker.speak("Hello again.")
    assert len(polly.calls) == 1  # not asked a second time


async def test_polly_over_its_ceiling_is_not_asked() -> None:
    polly = FakePolly()
    speaker, ledger = _speaker(polly, provider_daily_cap_usd='{"polly": 0.01}')
    ledger.record("polly", 1000, 0, 0.02)

    with pytest.raises(VoiceUnavailable) as refused:
        await speaker.speak("Hello.")

    assert refused.value.code == "daily_cap"
    assert polly.calls == []


# ── Transcribe ───────────────────────────────────────────────────────────────
def _transcript(result_id: str, text: str, *, partial: bool) -> bytes:
    result = {"ResultId": result_id, "IsPartial": partial, "Alternatives": [{"Transcript": text}]}
    body = {"Transcript": {"Results": [result]}}
    return eventstream.encode(
        {":event-type": "TranscriptEvent", ":message-type": "event"}, json.dumps(body).encode()
    )


class FakeTranscribe:
    """Hears audio events; answers with a partial, then the final words at the end."""

    def __init__(self) -> None:
        self.audio = bytearray()
        self.ended = False

    async def handler(self, socket: ServerConnection) -> None:
        decoder = eventstream.Decoder()
        said_partial = False
        async for data in socket:
            assert isinstance(data, bytes)
            for message in decoder.feed(data):
                assert message.headers[":event-type"] == "AudioEvent"
                if not message.payload:
                    self.ended = True
                    await socket.send(_transcript("r1", "What's the score?", partial=False))
                    return
                self.audio += message.payload
                if not said_partial:
                    said_partial = True
                    await socket.send(_transcript("r1", "What's the", partial=True))


@pytest.fixture
def transcribe() -> Iterator[tuple[FakeTranscribe, str]]:
    """The fake on its own thread and loop, so a sync test's app can reach it too."""
    fake = FakeTranscribe()
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    state: dict[str, Any] = {}

    async def run() -> None:
        state["stop"] = asyncio.Event()
        async with serve(fake.handler, "127.0.0.1", 0) as server:
            state["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            await state["stop"].wait()

    thread = threading.Thread(target=loop.run_until_complete, args=(run(),), daemon=True)
    thread.start()
    assert ready.wait(5)
    yield fake, f"ws://127.0.0.1:{state['port']}"
    loop.call_soon_threadsafe(state["stop"].set)
    thread.join(5)


def _ears(url: str, *, ledger: UsageLedger | None = None, **values: Any) -> Ears:
    async def opener(_signed: str) -> Any:
        # The signed URL is for AWS; the fake is on loopback.
        return await connect(url)

    return Ears(
        Settings(voice_enabled=True, **values),
        ledger=ledger or UsageLedger(),
        opener=opener,
        keys=lambda _settings: KEYS,
    )


async def _until(frames: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    for _ in range(300):
        for frame in frames:
            if frame["type"] == kind:
                return frame
        await asyncio.sleep(0.01)
    raise AssertionError(f"no {kind} in {frames}")


async def test_the_fans_voice_is_relayed_and_the_words_come_back(
    transcribe: tuple[FakeTranscribe, str],
) -> None:
    fake, url = transcribe
    ledger = UsageLedger()
    frames: list[dict[str, Any]] = []
    listening = _ears(url, ledger=ledger).open(listen_id=7, rate=16_000, send=frames.append)

    listening.feed(b"\x00\x01" * 1600)  # a tenth of a second
    partial = await _until(frames, "listen.partial")
    listening.feed(b"\x00\x01" * 1600)
    await listening.finish()
    final = await _until(frames, "listen.final")

    assert partial == {"type": "listen.partial", "id": 7, "text": "What's the"}
    assert final == {"type": "listen.final", "id": 7, "text": "What's the score?"}
    assert fake.ended and len(fake.audio) == 6400
    # Transcribe bills 15 seconds at least, per stream.
    assert ledger.spent_today("transcribe") == pytest.approx(15 * 0.024 / 60)


async def test_a_refused_stream_says_unavailable_and_stops_asking() -> None:
    class Refused(Exception):
        response = type("Response", (), {"status_code": 403})()

    async def opener(_url: str) -> Any:
        raise Refused("HTTP 403")

    ears = Ears(Settings(voice_enabled=True), opener=opener, keys=lambda _s: KEYS)
    frames: list[dict[str, Any]] = []
    listening = ears.open(listen_id=1, rate=16_000, send=frames.append)
    await listening.finish()

    error = await _until(frames, "listen.error")
    assert error["code"] == "unavailable"
    assert ears.refusal() == "unavailable"
    with pytest.raises(VoiceUnavailable):
        ears.open(listen_id=2, rate=16_000, send=frames.append)


async def test_a_stream_refused_by_iam_says_unavailable() -> None:
    # What AWS really does: the signature is accepted, the socket opens, and
    # the refusal arrives as an exception message in the stream.
    async def refuse(socket: ServerConnection) -> None:
        await socket.recv()
        body = json.dumps({"Message": "not authorized to perform StartStream…"}).encode()
        await socket.send(
            eventstream.encode(
                {":exception-type": "AccessDeniedException", ":message-type": "exception"}, body
            )
        )

    async with serve(refuse, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ears = _ears(f"ws://127.0.0.1:{port}")
        frames: list[dict[str, Any]] = []
        listening = ears.open(listen_id=5, rate=16_000, send=frames.append)
        listening.feed(b"\x00\x01" * 1600)
        error = await _until(frames, "listen.error")
        await listening.cancel()

    assert error["code"] == "unavailable"
    assert ears.refusal() == "unavailable"


async def test_an_utterance_longer_than_the_limit_is_answered_as_it_stands(
    transcribe: tuple[FakeTranscribe, str],
) -> None:
    fake, url = transcribe
    frames: list[dict[str, Any]] = []
    listening = _ears(url, listen_max_utterance_s=0.2).open(
        listen_id=3, rate=16_000, send=frames.append
    )

    for _ in range(5):
        listening.feed(b"\x00\x01" * 1600)

    assert (await _until(frames, "listen.final"))["text"] == "What's the score?"
    assert len(fake.audio) <= 0.2 * 16_000 * 2


def test_without_credentials_the_browser_listens_for_itself() -> None:
    ears = Ears(Settings(voice_enabled=True), keys=lambda _s: None)
    assert ears.refusal() == "no_credentials"
    assert Ears(Settings(voice_enabled=False), keys=lambda _s: KEYS).refusal() == "off"


# ── Through the app: /api/speech and /ws ─────────────────────────────────────
@pytest.fixture
def voiced(configure: Callable[..., Settings]) -> Iterator[tuple[TestClient, FakePolly]]:
    configure(voice_enabled="true", voice_chars_per_session="60")
    polly = FakePolly()
    with TestClient(create_app()) as test_client:
        registry = test_client.app.state.registry  # type: ignore[attr-defined]
        registry.speaker = Speaker(registry.settings, client_factory=lambda: polly)
        registry.ears = Ears(registry.settings, keys=lambda _s: None)
        yield test_client, polly


def _speech(client: TestClient, session: dict[str, Any], text: str, **headers: str) -> Any:
    return client.post(
        "/api/speech",
        json={"text": text},
        headers={
            "X-Fanmoot-Session": session["session_id"],
            "X-Fanmoot-Token": session["resume_token"],
            **headers,
        },
    )


def test_a_session_hears_alexa_speak(voiced: tuple[TestClient, FakePolly]) -> None:
    client, _ = voiced
    with client.websocket_connect("/ws") as socket:
        session = until(socket, "session")
        spoken = _speech(client, session, "Arsenal are two-one up.")

    assert session["voice"]["speak"] is True
    assert session["voice"]["listen"] is False  # no credentials for Transcribe here
    assert session["voice"]["voice"] == "Amy"
    assert spoken.status_code == 200
    assert spoken.headers["content-type"] == "audio/mpeg"
    assert spoken.headers["cache-control"] == "no-store"
    assert spoken.content == b"mp3:generative:Arsenal are two-one up."


def test_speech_needs_a_live_session_from_this_origin(
    voiced: tuple[TestClient, FakePolly],
) -> None:
    client, polly = voiced
    with client.websocket_connect("/ws") as socket:
        session = until(socket, "session")
        forged = _speech(client, {**session, "resume_token": "forged"}, "Hello.")
        elsewhere = _speech(client, session, "Hello.", Origin="https://evil.example")
        empty = _speech(client, session, "")

    assert forged.status_code == 401
    assert elsewhere.status_code == 403
    assert empty.status_code == 422
    assert polly.calls == []


def test_a_session_has_only_so_much_to_say(voiced: tuple[TestClient, FakePolly]) -> None:
    client, _ = voiced
    with client.websocket_connect("/ws") as socket:
        session = until(socket, "session")
        first = _speech(client, session, "x" * 50)
        second = _speech(client, session, "y" * 20)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "voice_cap"


def test_listening_over_the_socket(
    configure: Callable[..., Settings], transcribe: tuple[FakeTranscribe, str]
) -> None:
    fake, url = transcribe
    configure(voice_enabled="true")
    with TestClient(create_app()) as client:
        registry = client.app.state.registry  # type: ignore[attr-defined]
        registry.ears = _ears(url)
        with client.websocket_connect("/ws") as socket:
            assert until(socket, "session")["voice"]["listen"] is True
            socket.send_json({"type": "listen.start", "id": 4, "rate": 16_000})
            socket.send_bytes(b"\x00\x01" * 1600)
            partial = until(socket, "listen.partial")
            socket.send_json({"type": "listen.stop", "id": 4})
            final = until(socket, "listen.final")

    assert partial["text"] == "What's the"
    assert final == {"type": "listen.final", "id": 4, "text": "What's the score?"}
    assert fake.ended


def test_listening_needs_an_id_and_a_rate(client: TestClient) -> None:
    with client.websocket_connect("/ws") as socket:
        socket.send_json({"type": "listen.start", "id": 1, "rate": 2})
        bad = until(socket, "error")
        socket.send_json({"type": "listen.start", "id": 2, "rate": 16_000})
        off = until(socket, "listen.error")

    assert bad["code"] == "bad_listen"
    # FMD_VOICE_ENABLED is off for the suite: the browser is told to listen itself.
    assert off == {"type": "listen.error", "id": 2, "code": "off", "message": off["message"]}
