import asyncio
import logging
import threading
from typing import Any

import numpy as np
import pytest

from piotr_voice_hub.config import SttConfig, SttPartialConfig
from piotr_voice_hub.stt import whisper
from piotr_voice_hub.stt.transcript_preprocessor import TranscriptPreprocessor
from piotr_voice_hub.stt.whisper import (
    BYTES_PER_SECOND,
    FasterWhisperSpeechToText,
    FasterWhisperTranscriber,
    TextEnd,
    TextPartial,
    Transcriber,
    create_faster_whisper_transcriber,
    pcm_to_float,
)


def make_config(interval: float = 0.01, window: float = 4.0) -> SttConfig:
    return SttConfig(
        model="large-v3-turbo",
        device="cuda",
        compute_type="float16",
        beam_size=5,
        partial=SttPartialConfig(interval_seconds=interval, window_seconds=window, beam_size=1),
    )


class FakeTranscriber:
    def __init__(self, texts: list[str] | None = None) -> None:
        self.texts = texts or []
        self.calls: list[tuple[int, int]] = []  # (samples, beam_size)

    def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        assert audio.dtype == np.float32
        self.calls.append((len(audio), beam_size))
        return self.texts.pop(0) if self.texts else ""


def pcm(seconds: float) -> bytes:
    return bytes(int(seconds * BYTES_PER_SECOND))


async def started(transcriber: Transcriber, config: SttConfig) -> FasterWhisperSpeechToText:
    stt = FasterWhisperSpeechToText(config, lambda _c: transcriber)
    await stt.start()
    return stt


async def collect(session: Any) -> list[Any]:
    events = []
    while not isinstance(event := await session.receive_text(), TextEnd):
        events.append(event)
    return events


def test_start_loads_with_config_and_warms_both_beams() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        seen: list[SttConfig] = []

        def factory(config: SttConfig) -> Transcriber:
            seen.append(config)
            return fake

        config = make_config(window=0.5)
        stt = FasterWhisperSpeechToText(config, factory)
        await stt.start()
        await stt.start()  # idempotent
        assert seen == [config]
        assert fake.calls == [(8000, 1), (8000, 5)]

    asyncio.run(run())


def test_warmup_failure_propagates_and_stays_unstarted() -> None:
    class Failing(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            raise RuntimeError("CUDA failed")

    async def run() -> None:
        stt = FasterWhisperSpeechToText(make_config(), lambda _c: Failing())
        with pytest.raises(RuntimeError, match="CUDA failed"):
            await stt.start()
        with pytest.raises(AssertionError, match="start"):
            stt.create_streaming_session("s")

    asyncio.run(run())


def test_partials_then_final_over_the_whole_segment() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=0.02, window=1.0))
        fake.calls.clear()
        fake.texts = ["Ryszardzie", "Ryszardzie", "Ryszardzie, włącz", "Ryszardzie, włącz radio"]
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(0.5))
        while len(fake.calls) < 2:  # two partials on 0.5 s; the second repeats the text
            await asyncio.sleep(0.005)
        await session.send_audio(pcm(1.0))
        while len(fake.calls) < 3:
            await asyncio.sleep(0.005)
        final = await session.transcribe_final()
        events = await collect(session)

        assert final == "Ryszardzie, włącz radio"
        assert events == [
            TextPartial("Ryszardzie", 0.0, 0.5),  # the repeated text is not emitted again
            TextPartial("Ryszardzie, włącz", 0.5, 1.5),  # only the last window_seconds
        ]
        assert fake.calls[:3] == [(8000, 1), (8000, 1), (16000, 1)]
        assert fake.calls[-1] == (24000, 5)  # final: whole segment, final beam

    asyncio.run(run())


def test_final_applies_transcript_corrections() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=10.0))
        fake.texts = [" Ryszardzie, jaka jutro po kłodach? "]
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(1.0))
        assert await session.transcribe_final() == "Ryszardzie, jaka jutro pogoda?"
        assert await collect(session) == []

    asyncio.run(run())


def test_no_partial_without_audio_and_empty_text_is_not_emitted() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=0.01))
        fake.calls.clear()
        session = stt.create_streaming_session("s")
        await asyncio.sleep(0.05)
        assert fake.calls == []  # nothing to transcribe yet
        await session.send_audio(pcm(0.1))
        while not fake.calls:
            await asyncio.sleep(0.005)
        await session.transcribe_final()
        assert await collect(session) == []  # partials were all ""

    asyncio.run(run())


def test_stale_partial_is_dropped() -> None:
    release = threading.Event()
    entered = threading.Event()

    class Slow(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            if beam_size == 1 and not release.is_set():
                entered.set()
                release.wait(5)
            return "stale" if beam_size == 1 else "final"

    async def run() -> None:
        release.set()
        stt = await started(Slow(), make_config(interval=0.01))
        release.clear()
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(0.5))
        await asyncio.to_thread(entered.wait, 5)
        await session.send_audio(pcm(whisper.PARTIAL_MAX_BACKLOG_SECONDS + 0.5))
        release.set()
        assert await session.transcribe_final() == "final"
        assert await collect(session) == []

    asyncio.run(run())


def test_close_stops_partials_and_ends_the_stream() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=0.01))
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(0.2))
        await session.close()
        calls = len(fake.calls)
        await asyncio.sleep(0.05)
        assert len(fake.calls) == calls
        assert await session.receive_text() == TextEnd()
        with pytest.raises(AssertionError):
            await session.send_audio(pcm(0.1))

    asyncio.run(run())


def test_transcripts_are_logged_only_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=10.0))
        fake.texts = ["sekretna treść"]
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(0.1))
        await session.transcribe_final()

    with caplog.at_level(logging.INFO, logger="piotr_voice_hub.stt"):
        asyncio.run(run())
    assert "sekretna" not in caplog.text
    assert "STT model ready" in caplog.text


def test_pcm_to_float() -> None:
    samples = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()
    np.testing.assert_allclose(pcm_to_float(samples), [0.0, 0.5, -1.0, 32767 / 32768])
    with pytest.raises(AssertionError, match="even"):
        pcm_to_float(b"\x00")


def test_transcriber_passes_polish_and_beam_without_bias_and_joins_segments() -> None:
    class Segment:
        def __init__(self, text: str) -> None:
            self.text = text

    class Model:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, None]:
            self.kwargs = kwargs
            return iter([Segment(" Ryszardzie, "), Segment(" włącz radio. ")]), None

    model = Model()
    transcriber = FasterWhisperTranscriber(model)
    assert transcriber.transcribe(np.zeros(16, np.float32), 5) == "Ryszardzie, włącz radio."
    assert model.kwargs == {"language": "pl", "beam_size": 5}  # no hotwords/initial_prompt


def test_model_is_created_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import faster_whisper

    created: list[tuple[Any, ...]] = []

    def fake_model(name: str, *, device: str, compute_type: str) -> object:
        created.append((name, device, compute_type))
        return object()

    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_model)
    transcriber = create_faster_whisper_transcriber(make_config())
    assert created == [("large-v3-turbo", "cuda", "float16")]
    assert isinstance(transcriber, FasterWhisperTranscriber)


def test_transcript_preprocessor_uses_word_boundaries() -> None:
    preprocessor = TranscriptPreprocessor("test")
    assert preprocessor.preprocess("włącz tryb ventilacji") == "włącz tryb wentylacji"
    assert preprocessor.preprocess("superventilacji") == "superventilacji"


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("(muzyka)", ""),
        ("[MUZYKA]", ""),
        ("Ryszardzie (muzyka)", "Ryszardzie"),
        ("[Muzyka] Ryszardzie, włącz radio", "Ryszardzie, włącz radio"),
        ("Ryszardzie, (śmiech) włącz [brawa] radio", "Ryszardzie, włącz radio"),
        ("Ryszardzie, włącz radio", "Ryszardzie, włącz radio"),
        ("ustaw 50% (proszę", "ustaw 50% (proszę"),  # unbalanced: left alone
    ],
)
def test_transcript_preprocessor_drops_bracketed_notes(text: str, cleaned: str) -> None:
    assert TranscriptPreprocessor("test").preprocess(text) == cleaned


def test_session_final_drops_bracketed_notes() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=10.0))
        fake.texts = ["Ryszardzie (muzyka)"]
        session = stt.create_streaming_session("s")
        await session.send_audio(pcm(0.5))
        assert await session.transcribe_final() == "Ryszardzie"

    asyncio.run(run())
