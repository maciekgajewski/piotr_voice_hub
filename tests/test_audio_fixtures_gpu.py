"""Optional GPU tests: fixtures through VAD/segmenter, faster-whisper, and the wake matcher.

Opt-in: PIOTR_VOICE_HUB_GPU_TESTS=1 and a CUDA device, like tests/test_stt_gpu.py.
Run on piotr with tools/fixtures-gpu-test.sh (privately tagged image, no production ports).
"""

from __future__ import annotations

import asyncio
import os

import pytest
from recorded_fixtures import RecordedFixture, load_fixtures, segment_clip_async, words

from piotr_voice_hub.config import SttConfig, SttPartialConfig
from piotr_voice_hub.hub.wake import match_wake_phrase
from piotr_voice_hub.stt.whisper import BYTES_PER_SECOND, FasterWhisperSpeechToText, TextEnd

ctranslate2 = pytest.importorskip("ctranslate2")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.environ.get("PIOTR_VOICE_HUB_GPU_TESTS") != "1", reason="PIOTR_VOICE_HUB_GPU_TESTS != 1"
    ),
    pytest.mark.skipif(ctranslate2.get_cuda_device_count() == 0, reason="no CUDA device"),
]

# design.md §7 stt section; same settings as tests/test_stt_gpu.py
CONFIG = SttConfig(
    model="large-v3-turbo",
    device="cuda",
    compute_type="float16",
    beam_size=5,
    partial=SttPartialConfig(interval_seconds=0.75, window_seconds=4.0, beam_size=1),
)
MAX_AUDIO_SECONDS = 16.0
CHUNK_BYTES = 1024


async def transcribe(stt: FasterWhisperSpeechToText, name: str, pcm: bytes) -> str:
    if not pcm:
        return ""
    session = stt.create_streaming_session(name, MAX_AUDIO_SECONDS)
    for start in range(0, len(pcm), CHUNK_BYTES):
        await session.send_audio(pcm[start : start + CHUNK_BYTES])
        await asyncio.sleep(CHUNK_BYTES / BYTES_PER_SECOND)
    final = await session.transcribe_final()
    while not isinstance(await session.receive_text(), TextEnd):
        pass
    return final


def test_fixtures_through_vad_stt_and_matcher() -> None:
    fixtures = load_fixtures()

    async def run() -> None:
        stt = FasterWhisperSpeechToText(CONFIG)
        await stt.start()
        accepts = 0
        rejects = 0
        false_accepts = 0
        false_rejects = 0
        for fixture in fixtures:
            result = await _check_one(stt, fixture)
            if fixture.expected_wake:
                accepts += 1
                if not result:
                    false_rejects += 1
            else:
                rejects += 1
                if result:
                    false_accepts += 1
        print(
            f"\nT-028 GPU counts: expected accepts={accepts} rejects={rejects} "
            f"false_accepts={false_accepts} false_rejects={false_rejects}"
        )
        assert false_accepts == 0 and false_rejects == 0

    asyncio.run(run())


async def _check_one(stt: FasterWhisperSpeechToText, fixture: RecordedFixture) -> bool:
    segments = await segment_clip_async(fixture.pcm())
    if fixture.vad_mode == "exact":
        assert len(segments) == fixture.expected_vad_segments, fixture.id
    elif fixture.vad_mode == "at_least":
        assert fixture.expected_vad_segments is not None
        assert len(segments) >= fixture.expected_vad_segments, fixture.id

    if not segments:
        print(f"  {fixture.id}: no VAD segment")
        assert not fixture.expected_wake, fixture.id
        return False

    finals: list[str] = []
    for index, segment in enumerate(segments):
        final = await transcribe(stt, f"{fixture.id}-{index}", segment.pcm)
        finals.append(final)
        print(f"  {fixture.id}[{index}]: {final!r}")

    joined = " ".join(text for text in finals if text)
    matched = any(match_wake_phrase(text) is not None for text in [*finals, joined])
    if fixture.expected_wake:
        assert matched, (fixture.id, finals)
        if not fixture.soft_transcript and fixture.expected_transcript_words:
            found = [word for text in finals for word in words(text)]
            expected = fixture.expected_transcript_words
            prefix = found[: len(expected)]
            assert prefix == expected or set(expected) <= set(found), (
                fixture.id,
                finals,
            )
    else:
        assert not matched, (fixture.id, finals)
        if not fixture.soft_transcript and fixture.expected_transcript_words:
            found = [word for text in finals for word in words(text)]
            # Identity only: STT may inflect a later word (e.g. światło/światłu).
            assert fixture.expected_transcript_words[0] in found, (
                fixture.id,
                finals,
            )
    return matched
