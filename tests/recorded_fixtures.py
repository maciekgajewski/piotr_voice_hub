"""Load T-028 recorded-audio fixtures and run them through the VAD/segmenter path."""

from __future__ import annotations

import asyncio
import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from test_esphome_driver import TIMEOUT, arm, config, make, silence

from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    ListeningStarted,
    SpeechEnded,
    SpeechStarted,
)
from piotr_voice_hub.satellites.vad import FRAME_SAMPLES, SAMPLE_RATE, SileroVad, load_session

FIXTURES_DIR = Path(__file__).resolve().parent / "data" / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
# Enough trailing silence for the 0.8 s end hysteresis to close a segment.
DEFAULT_END_PAD_SECONDS = 1.2


@dataclass(frozen=True)
class RecordedFixture:
    id: str
    path: Path
    text: str
    expected_wake: bool
    expected_remainder: str | None
    expected_transcript_words: list[str] | None
    expected_vad_segments: int | None
    vad_mode: str
    soft_transcript: bool
    min_speech_start_seconds: float | None
    source: str
    sha256: str
    bytes: int
    duration_seconds: float
    notes: str
    raw: dict[str, Any]

    def pcm(self) -> bytes:
        with wave.open(str(self.path), "rb") as wav:
            assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (
                SAMPLE_RATE,
                2,
                1,
            ), f"{self.path} must be 16 kHz 16-bit mono"
            return wav.readframes(wav.getnframes())


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def load_fixtures() -> list[RecordedFixture]:
    manifest = load_manifest()
    fixtures: list[RecordedFixture] = []
    for item in manifest["fixtures"]:
        fixtures.append(
            RecordedFixture(
                id=item["id"],
                path=FIXTURES_DIR / item["path"],
                text=item["text"],
                expected_wake=item["expected_wake"],
                expected_remainder=item["expected_remainder"],
                expected_transcript_words=item["expected_transcript_words"],
                expected_vad_segments=item["expected_vad_segments"],
                vad_mode=item["vad_mode"],
                soft_transcript=item["soft_transcript"],
                min_speech_start_seconds=item["min_speech_start_seconds"],
                source=item["source"],
                sha256=item["sha256"],
                bytes=item["bytes"],
                duration_seconds=item["duration_seconds"],
                notes=item.get("notes", ""),
                raw=item,
            )
        )
    return fixtures


def words(text: str) -> list[str]:
    cleaned = []
    for raw in text.replace(",", " ").split():
        word = raw.strip(".,!?…:;").lower()
        if word:
            cleaned.append(word)
    return cleaned


@dataclass(frozen=True)
class Segment:
    pcm: bytes
    reason: str
    start_seconds: float | None


async def _collect_segments(pcm: bytes, end_pad_seconds: float) -> list[Segment]:
    mic, clients = make(SileroVad(load_session(), 0.8), config(end_silence=0.8))
    try:
        await arm(mic)
        await clients.last.audio(pcm + silence(end_pad_seconds))
        segments: list[Segment] = []
        while True:
            try:
                event = await asyncio.wait_for(mic.driver.wait_for_event(), TIMEOUT)
            except TimeoutError:
                break
            if isinstance(event, ListeningStarted):
                continue
            if not isinstance(event, SpeechStarted):
                # A leftover non-capture event ends the collection.
                break
            captured = bytearray()
            ended = None
            while True:
                nxt = await asyncio.wait_for(mic.driver.wait_for_event(), TIMEOUT)
                if isinstance(nxt, AudioChunk):
                    captured += nxt.data
                    continue
                assert isinstance(nxt, SpeechEnded)
                ended = nxt
                break
            assert ended is not None
            segments.append(Segment(bytes(captured), ended.reason, None))
        return segments
    finally:
        await mic.close()


async def segment_clip_async(
    pcm: bytes, end_pad_seconds: float = DEFAULT_END_PAD_SECONDS
) -> list[Segment]:
    """Feed PCM through the ESPHome driver with real Silero (the production segmenter)."""
    return await _collect_segments(pcm, end_pad_seconds)


def segment_clip(pcm: bytes, end_pad_seconds: float = DEFAULT_END_PAD_SECONDS) -> list[Segment]:
    """Synchronous wrapper for CPU tests that are not already in an event loop."""
    return asyncio.run(segment_clip_async(pcm, end_pad_seconds))


def vad_start_seconds(pcm: bytes) -> float | None:
    """Frame time of the first SPEECH_STARTED, or None if Silero stays quiet."""
    from piotr_voice_hub.satellites.vad import FRAME_BYTES, VadTransition

    vad = SileroVad(load_session(), 0.8)
    padded = pcm + silence(DEFAULT_END_PAD_SECONDS)
    for index in range(len(padded) // FRAME_BYTES):
        _, transition = vad.process_frame(padded[index * FRAME_BYTES : (index + 1) * FRAME_BYTES])
        if transition is VadTransition.SPEECH_STARTED:
            return index * FRAME_SAMPLES / SAMPLE_RATE
    return None
