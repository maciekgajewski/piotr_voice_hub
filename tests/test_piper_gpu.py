"""Optional integration test of the running Piper service (T-015) over the Wyoming protocol.

Opt-in: runs only with PIOTR_VOICE_HUB_PIPER_URI set (for example tcp://127.0.0.1:10201) and
the service up on piotr's GPU; tools/piper-gpu-test.sh sets it. The client side is a minimal
Wyoming reader (newline-terminated JSON header, then data and payload bytes), so the test needs
no Wyoming dependency in the hub.
"""

import asyncio
import json
import math
import os
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import pytest

from piotr_voice_hub.tts.piper_service import MAX_TEXT_CHARS

URI = os.environ.get("PIOTR_VOICE_HUB_PIPER_URI", "")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(not URI, reason="PIOTR_VOICE_HUB_PIPER_URI is not set"),
]

TIMEOUT_SECONDS = 30.0
SHORT = "Dobry wieczór, co mogę dla ciebie zrobić?"
LONG = (
    "W kuchni pali się jeszcze światło, a w salonie telewizor gra od godziny. Czy mam przypomnieć "
    "o wyłączeniu piekarnika, zanim pójdziesz spać, i nastawić budzik na jutro na wpół do siódmej "
    "rano?"
)


@dataclass
class Synthesis:
    rate: int = 0
    width: int = 0
    channels: int = 0
    audio: bytearray = field(default_factory=bytearray)
    first_audio_seconds: float = math.nan
    total_seconds: float = math.nan

    @property
    def audio_seconds(self) -> float:
        return len(self.audio) / (self.rate * self.width * self.channels)

    def rms(self) -> float:
        samples = struct.unpack(f"<{len(self.audio) // 2}h", self.audio)
        return math.sqrt(sum(s * s for s in samples) / len(samples))


async def read_event(reader: asyncio.StreamReader) -> tuple[str, dict, bytes]:
    header = json.loads(await reader.readline())
    data = dict(header.get("data") or {})
    if header.get("data_length"):
        data.update(json.loads(await reader.readexactly(header["data_length"])))
    payload = b""
    if header.get("payload_length"):
        payload = await reader.readexactly(header["payload_length"])
    return header["type"], data, payload


async def synthesize(text: str) -> Synthesis:
    uri = urlparse(URI)
    assert uri.scheme == "tcp" and uri.hostname and uri.port, URI
    result = Synthesis()
    start = time.monotonic()
    reader, writer = await asyncio.open_connection(uri.hostname, uri.port)
    try:
        event = {"type": "synthesize", "data": {"text": text}}
        writer.write(json.dumps(event).encode() + b"\n")
        await writer.drain()
        while True:
            kind, data, payload = await read_event(reader)
            if kind == "error":
                raise AssertionError(f"service error: {data}")
            if kind == "audio-start":
                result.rate, result.width, result.channels = (
                    data["rate"],
                    data["width"],
                    data["channels"],
                )
            elif kind == "audio-chunk":
                if not result.audio:
                    result.first_audio_seconds = time.monotonic() - start
                result.audio += payload
            elif kind == "audio-stop":
                result.total_seconds = time.monotonic() - start
                return result
    finally:
        writer.close()
        await writer.wait_closed()


def run(text: str) -> Synthesis:
    return asyncio.run(asyncio.wait_for(synthesize(text), TIMEOUT_SECONDS))


@pytest.mark.parametrize(
    ("text", "min_audio", "max_audio"),
    [(SHORT, 1.0, 6.0), (LONG, 8.0, 25.0)],
    ids=["short", "long"],
)
def test_service_synthesizes_polish_as_22050_hz_16_bit_mono(
    text: str, min_audio: float, max_audio: float
) -> None:
    for attempt in ("first", "second"):
        result = run(text)
        print(
            f"{attempt} {len(text)} chars: {result.audio_seconds:.2f} s of audio, first chunk "
            f"after {result.first_audio_seconds:.3f} s, done after {result.total_seconds:.3f} s"
        )
        assert (result.rate, result.width, result.channels) == (22050, 2, 1)
        assert min_audio <= result.audio_seconds <= max_audio
        assert result.rms() > 300  # speech, not silence
        assert result.total_seconds < 5.0  # warm: T-003 measured 0.1 s per inference


def first_event(synthesize: dict) -> tuple[str, dict]:
    async def request() -> tuple[str, dict]:
        uri = urlparse(URI)
        assert uri.hostname and uri.port
        reader, writer = await asyncio.open_connection(uri.hostname, uri.port)
        try:
            event = {"type": "synthesize", "data": synthesize}
            writer.write(json.dumps(event).encode() + b"\n")
            await writer.drain()
            kind, data, _ = await read_event(reader)
            return kind, data
        finally:
            writer.close()

    return asyncio.run(asyncio.wait_for(request(), TIMEOUT_SECONDS))


def test_another_voice_is_refused_without_a_download() -> None:
    kind, data = first_event({"text": SHORT, "voice": {"name": "en_US-lessac-medium"}})
    assert kind == "error"
    assert "only the preloaded voice" in data["text"]


def test_text_over_the_cap_is_refused() -> None:
    # One "sentence" (no sentence end) over the cap: wyoming-piper passes it in one call.
    text = ("bardzo " * (MAX_TEXT_CHARS // 7 + 10)).strip()
    assert len(text) > MAX_TEXT_CHARS
    kind, data = first_event({"text": text})
    assert kind == "error"
    assert data["code"] == "TextTooLongError"
    assert f"the limit per call is {MAX_TEXT_CHARS}" in data["text"]
    assert run(SHORT).audio_seconds > 1.0  # and the service still works
