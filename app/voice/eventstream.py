"""The AWS event-stream framing Transcribe's WebSocket speaks, both ways.

A message is a 12-byte prelude (total length, headers length, a CRC32 of the
two), the headers, the payload, and a CRC32 of all of it. Transcribe's docs
describe it in full ("Setting up a WebSocket stream"); it is small enough to
write here rather than take a dependency for, and the tests hold it to
botocore's own decoder.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

_PRELUDE = 12
_STRING = 7


class FrameError(Exception):
    """Bytes that are not an event-stream message."""


@dataclass
class Message:
    headers: dict[str, Any] = field(default_factory=dict)
    payload: bytes = b""


def _crc(data: bytes) -> bytes:
    return struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)


def encode(headers: dict[str, str], payload: bytes) -> bytes:
    """One message, with every header a string (all Transcribe needs)."""
    encoded = bytearray()
    for name, value in headers.items():
        key, text = name.encode(), value.encode()
        encoded += struct.pack(">B", len(key)) + key
        encoded += struct.pack(">BH", _STRING, len(text)) + text
    total = _PRELUDE + len(encoded) + len(payload) + 4
    prelude = struct.pack(">II", total, len(encoded))
    message = prelude + _crc(prelude) + bytes(encoded) + payload
    return message + _crc(message)


def audio_event(chunk: bytes) -> bytes:
    """An ``AudioEvent``. An empty one tells Transcribe the audio has ended."""
    return encode(
        {
            ":content-type": "application/octet-stream",
            ":event-type": "AudioEvent",
            ":message-type": "event",
        },
        chunk,
    )


class Decoder:
    """Messages out of a byte stream that may split or join them anywhere."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[Message]:
        self._buffer += data
        messages: list[Message] = []
        while len(self._buffer) >= _PRELUDE:
            total, headers_length = struct.unpack_from(">II", self._buffer, 0)
            if total < _PRELUDE + 4 or headers_length > total - _PRELUDE - 4:
                raise FrameError("impossible lengths in the prelude")
            if _crc(bytes(self._buffer[:8])) != bytes(self._buffer[8:12]):
                raise FrameError("the prelude's checksum does not match")
            if len(self._buffer) < total:
                break
            raw = bytes(self._buffer[:total])
            del self._buffer[:total]
            if _crc(raw[:-4]) != raw[-4:]:
                raise FrameError("the message's checksum does not match")
            headers = _headers(raw[_PRELUDE : _PRELUDE + headers_length])
            messages.append(Message(headers, raw[_PRELUDE + headers_length : -4]))
        return messages


def _headers(data: bytes) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    at = 0
    try:
        while at < len(data):
            (size,) = struct.unpack_from(">B", data, at)
            name = data[at + 1 : at + 1 + size].decode()
            at += 1 + size
            (kind,) = struct.unpack_from(">B", data, at)
            at += 1
            value: Any
            if kind in (0, 1):
                value = kind == 0
            elif kind == 2:
                (value,) = struct.unpack_from(">b", data, at)
                at += 1
            elif kind == 3:
                (value,) = struct.unpack_from(">h", data, at)
                at += 2
            elif kind in (4,):
                (value,) = struct.unpack_from(">i", data, at)
                at += 4
            elif kind in (5, 8):
                (value,) = struct.unpack_from(">q", data, at)
                at += 8
            elif kind in (6, _STRING):
                (length,) = struct.unpack_from(">H", data, at)
                raw = data[at + 2 : at + 2 + length]
                value = raw.decode() if kind == _STRING else raw
                at += 2 + length
            elif kind == 9:
                value = data[at : at + 16]
                at += 16
            else:
                raise FrameError(f"unknown header type {kind}")
            headers[name] = value
    except struct.error as exc:
        raise FrameError("headers run past their length") from exc
    return headers
