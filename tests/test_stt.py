import asyncio
import logging
import subprocess
import sys
import threading
from typing import Any, NoReturn

import numpy as np
import pytest

from piotr_voice_hub.config import SttConfig, SttPartialConfig
from piotr_voice_hub.stt import whisper
from piotr_voice_hub.stt.transcript_preprocessor import TranscriptPreprocessor
from piotr_voice_hub.stt.whisper import (
    BYTES_PER_SECOND,
    FasterWhisperSpeechToText,
    FasterWhisperStreamingSttSession,
    FasterWhisperTranscriber,
    TextEnd,
    TextPartial,
    Transcriber,
    create_faster_whisper_transcriber,
    pcm_to_float,
)

MAX_AUDIO_SECONDS = 16.0


def make_config(interval: float = 0.01, window: float = 4.0) -> SttConfig:
    return SttConfig(
        model="large-v3-turbo",
        device="cuda",
        compute_type="float16",
        beam_size=5,
        partial=SttPartialConfig(interval_seconds=interval, window_seconds=window, beam_size=1),
    )


class Fatal(Exception):
    pass


def fatal(message: str) -> NoReturn:
    raise Fatal(message)


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
    stt = FasterWhisperSpeechToText(config, lambda _c: transcriber, on_fatal=fatal)
    await stt.start()
    return stt


def session_of(stt: FasterWhisperSpeechToText) -> FasterWhisperStreamingSttSession:
    return stt.create_streaming_session("s", MAX_AUDIO_SECONDS)


async def collect(session: FasterWhisperStreamingSttSession) -> list[Any]:
    events = []
    while not isinstance(event := await session.receive_text(), TextEnd):
        events.append(event)
    return events


def test_start_loads_with_config_and_warms_both_beams_on_noise() -> None:
    async def run() -> None:
        seen: list[SttConfig] = []
        recorded: list[np.ndarray] = []

        class Recording(FakeTranscriber):
            def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
                recorded.append(audio)
                return super().transcribe(audio, beam_size)

        fake = Recording()

        def factory(config: SttConfig) -> Transcriber:
            seen.append(config)
            return fake

        config = make_config(window=0.5)
        stt = FasterWhisperSpeechToText(config, factory, on_fatal=fatal)
        await stt.start()
        await stt.start()  # idempotent
        assert seen == [config]
        assert fake.calls == [(8000, 1), (8000, 5)]
        assert 0 < np.abs(recorded[0]).max() < 0.01  # low-level noise, not all zeros

    asyncio.run(run())


def test_warmup_failure_propagates_and_stays_unstarted() -> None:
    class Failing(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            raise RuntimeError("CUDA failed")

    async def run() -> None:
        stt = FasterWhisperSpeechToText(make_config(), lambda _c: Failing(), on_fatal=fatal)
        with pytest.raises(RuntimeError, match="CUDA failed"):
            await stt.start()
        with pytest.raises(AssertionError, match="start"):
            session_of(stt)

    asyncio.run(run())


def test_partials_then_final_over_the_whole_segment() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=0.02, window=1.0))
        fake.calls.clear()
        fake.texts = ["Ryszardzie", "Ryszardzie", "Ryszardzie, włącz", "Ryszardzie, włącz radio"]
        session = session_of(stt)
        await session.send_audio(pcm(0.5))
        while len(fake.calls) < 1:
            await asyncio.sleep(0.005)
        await session.send_audio(pcm(0.25))
        while len(fake.calls) < 2:  # the second partial repeats the text
            await asyncio.sleep(0.005)
        await session.send_audio(pcm(0.75))
        while len(fake.calls) < 3:
            await asyncio.sleep(0.005)
        final = await session.transcribe_final()
        events = await collect(session)

        assert final == "Ryszardzie, włącz radio"
        assert events == [
            TextPartial("Ryszardzie", 0.0, 0.5),  # the repeated text is not emitted again
            TextPartial("Ryszardzie, włącz", 0.5, 1.5),  # only the last window_seconds
        ]
        assert fake.calls[:3] == [(8000, 1), (12000, 1), (16000, 1)]
        assert fake.calls[-1] == (24000, 5)  # final: whole segment, final beam

    asyncio.run(run())


def test_no_partial_without_new_audio() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=0.01))
        fake.calls.clear()
        session = session_of(stt)
        await asyncio.sleep(0.05)
        assert fake.calls == []  # nothing to transcribe yet
        await session.send_audio(pcm(0.1))
        await asyncio.sleep(0.1)  # ~10 ticks, one new chunk of audio
        assert len(fake.calls) == 1
        await session.transcribe_final()
        assert await collect(session) == []  # partials were all ""

    asyncio.run(run())


def test_final_applies_transcript_cleanup() -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=10.0))
        fake.texts = [" Ryszardzie (muzyka), jaka jutro po kłodach? "]
        session = session_of(stt)
        await session.send_audio(pcm(1.0))
        assert await session.transcribe_final() == "Ryszardzie, jaka jutro pogoda?"
        assert await collect(session) == []

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
        session = session_of(stt)
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
        session = session_of(stt)
        await session.send_audio(pcm(0.2))
        await session.close()
        calls = len(fake.calls)
        await asyncio.sleep(0.05)
        assert len(fake.calls) == calls
        assert await session.receive_text() == TextEnd()
        with pytest.raises(AssertionError):
            await session.send_audio(pcm(0.1))

    asyncio.run(run())


def test_partial_failure_is_fatal_and_surfaces_to_the_reader() -> None:
    class FailingPartials(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            if beam_size == 1 and not audio.any():  # session audio is silence; warm-up is noise
                raise RuntimeError("CUDA error: out of memory")
            return ""

    async def run() -> None:
        stt = FasterWhisperSpeechToText(
            make_config(window=0.1), lambda _c: FailingPartials(), on_fatal=fatal
        )
        await stt.start()
        session = session_of(stt)
        await session.send_audio(pcm(0.5))
        with pytest.raises(Fatal, match="partial transcription failed.*out of memory"):
            await asyncio.wait_for(session.receive_text(), 2)
        with pytest.raises(Fatal):
            await session.transcribe_final()

    asyncio.run(run())


def test_close_retrieves_a_partial_failure() -> None:
    class FailingPartials(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            if len(audio) == 1600:
                raise RuntimeError("boom")
            return ""

    async def run() -> None:
        stt = await started(FailingPartials(), make_config(interval=0.01))
        session = session_of(stt)
        await session.send_audio(pcm(0.1))
        await asyncio.sleep(0.1)
        await session.close()  # no "Task exception was never retrieved"
        with pytest.raises(Fatal):
            await session.receive_text()

    asyncio.run(run())


def test_hung_gpu_call_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    class Hung(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            if beam_size == 5 and len(audio) == 8000:
                release.wait(5)
            return ""

    async def run() -> None:
        stt = await started(Hung(), make_config(interval=10.0))
        monkeypatch.setattr(whisper, "GPU_CALL_TIMEOUT_SECONDS", 0.05)
        session = session_of(stt)
        await session.send_audio(pcm(0.5))
        with pytest.raises(Fatal, match="final transcription timed out"):
            await session.transcribe_final()
        release.set()

    asyncio.run(run())


def test_hung_model_load_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def factory(_config: SttConfig) -> Transcriber:
        release.wait(5)
        return FakeTranscriber()

    async def run() -> None:
        monkeypatch.setattr(whisper, "LOAD_TIMEOUT_SECONDS", 0.05)
        stt = FasterWhisperSpeechToText(make_config(), factory, on_fatal=fatal)
        with pytest.raises(Fatal, match="load or warm-up timed out"):
            await stt.start()
        release.set()

    asyncio.run(run())


def test_concurrent_sessions_share_one_model_partials_skip_finals_wait() -> None:
    class Serial(FakeTranscriber):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.guard = threading.Lock()

        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            threading.Event().wait(0.01)  # a slow model
            with self.guard:
                self.active -= 1
                self.calls.append((len(audio), beam_size))
            return "final" if beam_size == 5 else f"partial {len(audio)}"

    async def run() -> None:
        fake = Serial()
        stt = await started(fake, make_config(interval=0.005))
        fake.calls.clear()
        sessions = [stt.create_streaming_session(f"s{i}", MAX_AUDIO_SECONDS) for i in range(4)]
        for _ in range(20):
            for session in sessions:
                await session.send_audio(pcm(0.032))
            await asyncio.sleep(0.005)
        finals = await asyncio.wait_for(
            asyncio.gather(*(s.transcribe_final() for s in sessions)), 5
        )
        assert finals == ["final"] * 4
        assert fake.max_active == 1  # never two model calls at once
        assert [beam for _, beam in fake.calls].count(5) == 4  # every final ran
        partial_calls = [beam for _, beam in fake.calls].count(1)
        assert 0 < partial_calls < 20 * 4  # some partial ticks were skipped while busy
        for session in sessions:
            await collect(session)

    asyncio.run(run())


def test_audio_and_events_are_bounded() -> None:
    async def run() -> None:
        stt = await started(FakeTranscriber(), make_config(interval=0.75))
        session = stt.create_streaming_session("s", 1.0)
        await session.send_audio(pcm(1.0))
        with pytest.raises(AssertionError, match="longer than 1.0 s"):
            await session.send_audio(pcm(0.032))
        assert session._events.maxsize == 4  # ceil(1.0 / 0.75) + 2
        await session.close()

    asyncio.run(run())


def test_transcripts_are_logged_only_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        fake = FakeTranscriber()
        stt = await started(fake, make_config(interval=10.0))
        fake.texts = ["sekretna treść, po kłodach"]
        session = session_of(stt)
        await session.send_audio(pcm(0.1))
        await session.transcribe_final()

    with caplog.at_level(logging.INFO, logger="piotr_voice_hub.stt"):
        asyncio.run(run())
    assert "sekretna" not in caplog.text
    assert "replacement" not in caplog.text
    assert "STT model ready" in caplog.text


def test_pcm_to_float() -> None:
    samples = np.array([0, 16384, -32768, 32767], dtype="<i2").tobytes()
    np.testing.assert_allclose(pcm_to_float(samples), [0.0, 0.5, -1.0, 32767 / 32768])
    with pytest.raises(AssertionError, match="even"):
        pcm_to_float(b"\x00")


class Segment:
    def __init__(self, text: str) -> None:
        self.text = text


class Model:
    def __init__(self, segments: list[Segment]) -> None:
        self.segments = segments
        self.kwargs: dict[str, Any] = {}

    def transcribe(self, audio: np.ndarray, **kwargs: Any) -> tuple[Any, None]:
        self.kwargs = kwargs
        return iter(self.segments), None


def test_transcriber_uses_the_old_projects_decoding_parameters() -> None:
    model = Model([Segment(" Ryszardzie, "), Segment(" włącz radio. ")])
    transcriber = FasterWhisperTranscriber(model)
    assert transcriber.transcribe(np.zeros(16, np.float32), 5) == "Ryszardzie, włącz radio."
    # Exactly what piotr_the_ai_server passed; no hotwords, no initial_prompt.
    assert model.kwargs == {"language": "pl", "beam_size": 5}


def test_model_is_created_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[Any, ...]] = []

    def fake_model(name: str, *, device: str, compute_type: str) -> object:
        created.append((name, device, compute_type))
        return object()

    monkeypatch.setattr(whisper, "WhisperModel", fake_model)
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
        ("♪", ""),
        ("♪ ♪ ♪", ""),
        ("  ...  ", ""),
        ("Ryszardzie ♪ włącz radio", "Ryszardzie ♪ włącz radio"),
    ],
)
def test_transcript_preprocessor_drops_notes_and_symbol_only_text(text: str, cleaned: str) -> None:
    assert TranscriptPreprocessor("test").preprocess(text) == cleaned


class Concurrency(FakeTranscriber):
    """Counts overlapping model calls; partials (beam 1) on session audio block until released."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()
        self.partial_started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if beam_size == 1 and not audio.any():  # session audio is silence; warm-up is noise
                self.partial_started.set()
                self.release.wait(5)
            return "final" if beam_size == 5 else "partial"
        finally:
            with self.guard:
                self.active -= 1


def test_close_mid_partial_keeps_the_model_locked_until_the_call_finishes() -> None:
    async def run() -> None:
        fake = Concurrency()
        stt = await started(fake, make_config(interval=0.01))
        a = session_of(stt)
        b = stt.create_streaming_session("b", MAX_AUDIO_SECONDS)
        await a.send_audio(pcm(0.5))
        await asyncio.to_thread(fake.partial_started.wait, 5)
        await a.close()  # cancels a's worker while its partial is still running
        await b.send_audio(pcm(0.5))
        final = asyncio.create_task(b.transcribe_final())
        await asyncio.sleep(0.05)
        assert not final.done()  # waits for a's call to finish, not for a's cancellation
        fake.release.set()
        assert await asyncio.wait_for(final, 2) == "final"
        assert fake.max_active == 1

    asyncio.run(run())


def test_full_event_queue_drops_partials_but_keeps_the_end_marker() -> None:
    class Changing(FakeTranscriber):
        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            return "final" if beam_size == 5 else f"partial {len(audio)}"

    async def run() -> None:
        stt = await started(Changing(), make_config(interval=0.01))
        session = stt.create_streaming_session("s", 0.04)  # maxsize = ceil(0.04 / 0.01) + 2 = 6
        for _ in range(40):  # far more distinct partials than slots, and nobody reads yet
            await session.send_audio(bytes(32))
            await asyncio.sleep(0.015)
        assert await session.transcribe_final() == "final"
        events = await asyncio.wait_for(collect(session), 1)
        assert 0 < len(events) <= 5  # one slot stays free for TextEnd

    asyncio.run(run())


def test_final_waiting_behind_a_timed_out_partial_is_fatal_not_a_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        fake = Concurrency()
        stt = await started(fake, make_config(interval=0.01))
        monkeypatch.setattr(whisper, "GPU_CALL_TIMEOUT_SECONDS", 0.2)
        session = session_of(stt)
        await session.send_audio(pcm(0.5))
        await asyncio.to_thread(fake.partial_started.wait, 5)  # the partial is wedged
        await asyncio.sleep(0.1)  # so the partial's 0.2 s budget clearly runs out first
        with pytest.raises(Fatal, match="partial transcription timed out"):
            await asyncio.wait_for(session.transcribe_final(), 2)
        fake.release.set()

    asyncio.run(run())


def test_exit_process_logs_critical_and_exits_70_with_a_wedged_thread() -> None:
    code = """
import logging, threading
from piotr_voice_hub.stt.whisper import exit_process
logging.basicConfig(level=logging.INFO)
threading.Thread(target=threading.Event().wait).start()  # non-daemon, never finishes
exit_process("STT final transcription timed out")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 70
    assert "CRITICAL" in result.stderr
    assert "STT final transcription timed out; exiting so the container restarts" in result.stderr


def test_model_error_after_close_is_still_fatal() -> None:
    fatal_messages: list[str] = []

    def record(message: str) -> NoReturn:
        fatal_messages.append(message)
        raise Fatal(message)

    class FailsAfterRelease(FakeTranscriber):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
            if beam_size == 1 and not audio.any():  # session audio is silence; warm-up is noise
                self.started.set()
                self.release.wait(5)
                raise RuntimeError("CUDA error")
            return "warm"

    async def run() -> None:
        fake = FailsAfterRelease()
        stt = FasterWhisperSpeechToText(make_config(), lambda _c: fake, on_fatal=record)
        await stt.start()
        session = session_of(stt)
        await session.send_audio(pcm(0.5))
        await asyncio.to_thread(fake.started.wait, 5)
        await session.close()  # abandons the running partial
        fake.release.set()
        for _ in range(100):
            if fatal_messages:
                break
            await asyncio.sleep(0.01)

    loop_errors: list[dict[str, Any]] = []

    async def main() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _l, c: loop_errors.append(c))
        await run()

    asyncio.run(main())
    assert fatal_messages == [
        "STT model call failed after its session closed: RuntimeError('CUDA error')"
    ]
