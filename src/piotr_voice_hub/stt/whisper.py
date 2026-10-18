"""Streaming Polish STT on faster-whisper (design.md §4.1 step 3, §4.2).

Copied from piotr_the_ai_server `ai_server/speech_to_text/faster_whisper.py` and trimmed to
the hub's one use: a streaming session per speech segment that gives partials every
`stt.partial.interval_seconds` over the last `window_seconds`, plus a final transcript.
Changes from the old code: hub `SttConfig`, language fixed to Polish, audio passed to the
model as an array (no temp WAV), no extra partial after audio end, no non-streaming session.

- No hotwords or initial_prompt: biasing with "Ryszardzie" made the model echo the wake word
  for silence, noise and the start of ordinary speech (measured, T-008 log).
- One model, one lock: a partial skips its tick while the model is busy; a final waits. The
  lock is released when the model thread finishes, not when its awaiter is cancelled, so a
  `close()` mid-partial can't let a second call overlap the one still running.
- Decoding parameters are faster-whisper's defaults, exactly as the old project ran them
  (language and beam only); `TranscriptPreprocessor` cleans the text.
- Every GPU call has a timeout. A timeout or a model error is fatal for the process (a wedged
  CUDA call can't be cancelled and the model is shared): log CRITICAL and exit; Docker restarts.
- VRAM is freed only when the process exits.
"""

import asyncio
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol

import numpy as np
from faster_whisper import WhisperModel

from piotr_voice_hub.config import SttConfig
from piotr_voice_hub.stt.transcript_preprocessor import TranscriptPreprocessor

LANGUAGE = "pl"
SAMPLE_RATE = 16000  # 16-bit mono PCM, as the satellites send it
BYTES_PER_SECOND = SAMPLE_RATE * 2
# Drop a partial if this much new audio arrived while it was decoding (it is stale).
PARTIAL_MAX_BACKLOG_SECONDS = 2.0
# Generous bounds: a final over 16 s of audio takes well under 1 s on piotr.
LOAD_TIMEOUT_SECONDS = 900.0  # the first start downloads the model (1.6 GB)
WARMUP_TIMEOUT_SECONDS = 120.0  # the first GPU call took 7.8 s (design.md §4.3)
GPU_CALL_TIMEOUT_SECONDS = 30.0  # one transcription, including the wait for the model lock

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextPartial:
    text: str
    audio_start_seconds: float
    audio_end_seconds: float


@dataclass(frozen=True)
class TextEnd:
    pass


type StreamingTextEvent = TextPartial | TextEnd


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        """Transcribe 16 kHz mono float32 audio in [-1, 1]. Blocking."""
        ...


type TranscriberFactory = Callable[[SttConfig], Transcriber]
type FatalHandler = Callable[[str], NoReturn]


def exit_process(message: str) -> NoReturn:
    _logger.critical("%s; exiting so the container restarts", message)
    logging.shutdown()
    os._exit(70)  # not sys.exit: a wedged executor thread would block interpreter shutdown


class FasterWhisperTranscriber:
    def __init__(self, model: Any) -> None:
        self._model = model

    def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        segments, _info = self._model.transcribe(audio, language=LANGUAGE, beam_size=beam_size)
        return " ".join(segment.text.strip() for segment in segments).strip()


def create_faster_whisper_transcriber(config: SttConfig) -> Transcriber:
    model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
    return FasterWhisperTranscriber(model)


def pcm_to_float(pcm: bytes) -> np.ndarray:
    assert len(pcm) % 2 == 0, f"16-bit PCM must have an even byte count, got {len(pcm)}"
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def audio_seconds(byte_count: int) -> float:
    return byte_count / BYTES_PER_SECOND


def warmup_audio(seconds: float) -> np.ndarray:
    """Low-level noise: a realistic decode path (all-zero audio is a degenerate input)."""
    noise = np.random.default_rng(0).normal(0.0, 30.0, int(seconds * SAMPLE_RATE))
    return (noise / 32768.0).astype(np.float32)


class FasterWhisperSpeechToText:
    """Owns the one shared model and its lock. `start()` loads and warms it before arming."""

    def __init__(
        self,
        config: SttConfig,
        transcriber_factory: TranscriberFactory = create_faster_whisper_transcriber,
        on_fatal: FatalHandler = exit_process,
    ) -> None:
        self._config = config
        self._transcriber_factory = transcriber_factory
        self._on_fatal = on_fatal
        self._lock = asyncio.Lock()
        self._transcriber: Transcriber | None = None

    async def start(self) -> None:
        if self._transcriber is not None:
            return
        config = self._config
        started_at = time.monotonic()
        _logger.info(
            "loading STT model model=%s device=%s compute_type=%s",
            config.model,
            config.device,
            config.compute_type,
        )
        try:
            async with asyncio.timeout(LOAD_TIMEOUT_SECONDS):
                transcriber = await asyncio.to_thread(self._transcriber_factory, config)
            audio = warmup_audio(config.partial.window_seconds)
            for beam_size in sorted({config.partial.beam_size, config.beam_size}):
                async with asyncio.timeout(WARMUP_TIMEOUT_SECONDS):
                    await asyncio.to_thread(transcriber.transcribe, audio, beam_size)
        except TimeoutError:
            self._on_fatal("STT model load or warm-up timed out")
        self._transcriber = transcriber
        _logger.info("STT model ready duration_seconds=%.2f", time.monotonic() - started_at)

    def create_streaming_session(
        self, session_id: str, max_audio_seconds: float
    ) -> "FasterWhisperStreamingSttSession":
        """`max_audio_seconds`: the segment cap (max_segment_seconds plus the pre-roll)."""
        assert self._transcriber is not None, "start() the STT before creating sessions"
        return FasterWhisperStreamingSttSession(
            session_id,
            self._transcriber,
            self._lock,
            self._config,
            max_audio_seconds,
            self._on_fatal,
        )


class FasterWhisperStreamingSttSession:
    """One speech segment. Create it inside the running event loop.

    Feed audio with `send_audio` (at most `max_audio_seconds`). Read partials with
    `receive_text` until `TextEnd`; it raises if the partial worker failed.
    `transcribe_final()` ends the audio, waits for an in-flight partial, emits `TextEnd`,
    and returns the final transcript over the whole segment. `close()` abandons it.
    """

    def __init__(
        self,
        session_id: str,
        transcriber: Transcriber,
        lock: asyncio.Lock,
        config: SttConfig,
        max_audio_seconds: float,
        on_fatal: FatalHandler,
    ) -> None:
        self._transcriber = transcriber
        self._lock = lock
        self._on_fatal = on_fatal
        self._final_beam_size = config.beam_size
        self._partial_beam_size = config.partial.beam_size
        self._partial_interval_seconds = config.partial.interval_seconds
        self._partial_window_bytes = int(config.partial.window_seconds * BYTES_PER_SECOND) // 2 * 2
        self._max_audio_bytes = int(max_audio_seconds * BYTES_PER_SECOND)
        self._audio = bytearray()
        self._audio_ended = asyncio.Event()
        self._closed = False
        self._last_partial_text = ""
        self._last_partial_end_byte = 0
        # Sized for real-time audio (one partial per interval) plus TextEnd. Partials are
        # best-effort: one that doesn't fit is dropped, and the last slot is kept for TextEnd.
        max_events = math.ceil(max_audio_seconds / config.partial.interval_seconds) + 2
        self._events: asyncio.Queue[StreamingTextEvent] = asyncio.Queue(maxsize=max_events)
        self._stream_ended = False
        self._error: BaseException | None = None
        self._preprocessor = TranscriptPreprocessor(session_id)
        self._logger = logging.getLogger(f"{__name__}.session[{session_id}]")
        self._worker = asyncio.create_task(self._partial_worker())

    async def send_audio(self, pcm: bytes) -> None:
        assert not self._audio_ended.is_set(), "cannot send STT audio after the audio ended"
        assert len(self._audio) + len(pcm) <= self._max_audio_bytes, (
            f"STT segment longer than {audio_seconds(self._max_audio_bytes):.1f} s"
        )
        self._audio.extend(pcm)

    async def receive_text(self) -> StreamingTextEvent:
        event = await self._events.get()
        if isinstance(event, TextEnd) and self._error is not None:
            raise self._error
        return event

    async def transcribe_final(self) -> str:
        assert not self._closed, "transcribe_final() after close()"
        self._audio_ended.set()
        try:
            async with asyncio.timeout(GPU_CALL_TIMEOUT_SECONDS):
                await self._worker  # re-raises a partial failure
        except TimeoutError:
            self._on_fatal("STT partial worker did not finish")
        text = await self._transcribe(bytes(self._audio), self._final_beam_size, "final")
        assert text is not None
        return text

    async def close(self) -> None:
        self._closed = True
        self._audio_ended.set()
        if not self._worker.done():
            self._worker.cancel()
        try:
            async with asyncio.timeout(GPU_CALL_TIMEOUT_SECONDS):
                await self._worker
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # already handled by on_fatal; retrieved here so it is not left on the task
        self._end_stream()  # also when the worker was cancelled before it ever ran

    def _end_stream(self) -> None:
        if not self._stream_ended:
            self._events.put_nowait(TextEnd())  # a slot is always free: partials leave one
            self._stream_ended = True

    async def _partial_worker(self) -> None:
        try:
            while True:
                try:
                    async with asyncio.timeout(self._partial_interval_seconds):
                        await self._audio_ended.wait()
                    return  # audio ended: the final transcript replaces further partials
                except TimeoutError:
                    pass
                end_byte = len(self._audio)
                if end_byte == self._last_partial_end_byte:
                    continue  # no new audio
                start_byte = max(0, end_byte - self._partial_window_bytes)
                audio = bytes(self._audio[start_byte:end_byte])
                text = await self._transcribe(
                    audio, self._partial_beam_size, "partial", skip_if_busy=True
                )
                if text is None:
                    continue  # the model was busy; try again next tick
                self._last_partial_end_byte = end_byte
                backlog = audio_seconds(len(self._audio) - end_byte)
                if backlog > PARTIAL_MAX_BACKLOG_SECONDS:
                    self._logger.debug("dropping stale partial backlog_seconds=%.2f", backlog)
                    continue
                if text and text != self._last_partial_text:
                    if self._events.qsize() >= self._events.maxsize - 1:
                        self._logger.debug("event queue full; dropping a partial")
                        continue
                    self._last_partial_text = text
                    self._events.put_nowait(
                        TextPartial(text, audio_seconds(start_byte), audio_seconds(end_byte))
                    )
        except Exception as error:
            self._error = error
            raise
        finally:
            self._end_stream()

    async def _transcribe(
        self, pcm: bytes, beam_size: int, kind: str, *, skip_if_busy: bool = False
    ) -> str | None:
        # A call that hangs after close() keeps the lock, so partials skip quietly until the
        # next final's lock wait hits GPU_CALL_TIMEOUT_SECONDS and exits the process.
        if skip_if_busy and self._lock.locked():
            self._logger.debug("STT model busy; skipping this partial")
            return None
        started_at = time.monotonic()
        try:
            async with asyncio.timeout(GPU_CALL_TIMEOUT_SECONDS):
                raw = await self._run_model(pcm, beam_size)
        except TimeoutError:
            self._on_fatal(f"STT {kind} transcription timed out")
        except Exception as error:
            self._on_fatal(f"STT {kind} transcription failed: {error!r}")
        text = self._preprocessor.preprocess(raw)
        self._logger.debug("STT %s transcript raw=%r text=%r", kind, raw, text)
        self._logger.debug(
            "STT %s done audio_seconds=%.2f beam_size=%s duration_seconds=%.2f",
            kind,
            audio_seconds(len(pcm)),
            beam_size,
            time.monotonic() - started_at,
        )
        return text

    async def _run_model(self, pcm: bytes, beam_size: int) -> str:
        """One model call under the lock; the lock is released when the thread finishes."""
        await self._lock.acquire()
        try:
            call = asyncio.ensure_future(
                asyncio.to_thread(self._transcriber.transcribe, pcm_to_float(pcm), beam_size)
            )
        except BaseException:
            self._lock.release()
            raise
        call.add_done_callback(self._model_call_done)
        # shield: cancelling the awaiter (close(), a timeout) must not end the call's lock hold.
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError:
            call.add_done_callback(self._abandoned_call_done)
            raise

    def _model_call_done(self, _call: "asyncio.Future[str]") -> None:
        self._lock.release()

    def _abandoned_call_done(self, call: "asyncio.Future[str]") -> None:
        # Nobody awaits this call any more (close() cancelled it), but a model error is still
        # fatal: fail loudly instead of leaving a broken model for the next session.
        if not call.cancelled() and (error := call.exception()) is not None:
            self._on_fatal(f"STT model call failed after its session closed: {error!r}")
