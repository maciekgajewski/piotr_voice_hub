"""Streaming Polish STT on faster-whisper (design.md §4.1 step 3, §4.2).

Copied from piotr_the_ai_server `ai_server/speech_to_text/faster_whisper.py` and trimmed to
the hub's one use: a streaming session per speech segment that gives partials every
`stt.partial.interval_seconds` over the last `window_seconds`, plus a final transcript.
Changes from the old code: hub `SttConfig`, language fixed to Polish, audio passed to the
model as an array (no temp WAV), no extra partial after audio end, no non-streaming session.

No hotwords or initial_prompt: biasing with "Ryszardzie" made the model echo the wake word for
silence, noise and the start of ordinary speech (measured, T-008 log). Unbiased, the wake clip
is still transcribed correctly.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from piotr_voice_hub.config import SttConfig
from piotr_voice_hub.stt.transcript_preprocessor import TranscriptPreprocessor

LANGUAGE = "pl"
SAMPLE_RATE = 16000  # 16-bit mono PCM, as the satellites send it
BYTES_PER_SECOND = SAMPLE_RATE * 2
# Drop a partial if this much new audio arrived while it was decoding (it is stale).
PARTIAL_MAX_BACKLOG_SECONDS = 2.0

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


class FasterWhisperTranscriber:
    def __init__(self, model: Any) -> None:
        self._model = model

    def transcribe(self, audio: np.ndarray, beam_size: int) -> str:
        segments, _info = self._model.transcribe(audio, language=LANGUAGE, beam_size=beam_size)
        return " ".join(segment.text.strip() for segment in segments).strip()


def create_faster_whisper_transcriber(config: SttConfig) -> Transcriber:
    from faster_whisper import WhisperModel  # heavy import, only when actually loading

    model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
    return FasterWhisperTranscriber(model)


def pcm_to_float(pcm: bytes) -> np.ndarray:
    assert len(pcm) % 2 == 0, f"16-bit PCM must have an even byte count, got {len(pcm)}"
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def audio_seconds(byte_count: int) -> float:
    return byte_count / BYTES_PER_SECOND


class FasterWhisperSpeechToText:
    """Owns the one shared model. `start()` loads and warms it; call it before arming."""

    def __init__(
        self,
        config: SttConfig,
        transcriber_factory: TranscriberFactory = create_faster_whisper_transcriber,
    ) -> None:
        self._config = config
        self._transcriber_factory = transcriber_factory
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
        transcriber = await asyncio.to_thread(self._transcriber_factory, config)
        # The first call on the GPU is slow (7.8 s measured, design.md §4.3): warm both beams.
        warmup = np.zeros(int(config.partial.window_seconds * SAMPLE_RATE), dtype=np.float32)
        for beam_size in sorted({config.partial.beam_size, config.beam_size}):
            await asyncio.to_thread(transcriber.transcribe, warmup, beam_size)
        self._transcriber = transcriber
        _logger.info("STT model ready duration_seconds=%.2f", time.monotonic() - started_at)

    def create_streaming_session(self, session_id: str) -> "FasterWhisperStreamingSttSession":
        assert self._transcriber is not None, "start() the STT before creating sessions"
        return FasterWhisperStreamingSttSession(session_id, self._transcriber, self._config)

    async def close(self) -> None:
        self._transcriber = None


class FasterWhisperStreamingSttSession:
    """One speech segment. Create it inside the running event loop.

    Feed audio with `send_audio`. Read partials with `receive_text` until `TextEnd`.
    `transcribe_final()` ends the audio, waits for an in-flight partial, emits `TextEnd`,
    and returns the final transcript over the whole segment. `close()` abandons it.
    """

    def __init__(self, session_id: str, transcriber: Transcriber, config: SttConfig) -> None:
        self._transcriber = transcriber
        self._final_beam_size = config.beam_size
        self._partial_beam_size = config.partial.beam_size
        self._partial_interval_seconds = config.partial.interval_seconds
        self._partial_window_bytes = int(config.partial.window_seconds * BYTES_PER_SECOND) // 2 * 2
        self._audio = bytearray()
        self._audio_ended = asyncio.Event()
        self._closed = False
        self._last_partial_text = ""
        self._events: asyncio.Queue[StreamingTextEvent] = asyncio.Queue()
        self._stream_ended = False
        self._preprocessor = TranscriptPreprocessor(session_id)
        self._logger = logging.getLogger(f"{__name__}.session[{session_id}]")
        self._worker = asyncio.create_task(self._partial_worker())

    async def send_audio(self, pcm: bytes) -> None:
        assert not self._audio_ended.is_set(), "cannot send STT audio after the audio ended"
        self._audio.extend(pcm)

    async def receive_text(self) -> StreamingTextEvent:
        return await self._events.get()

    async def transcribe_final(self) -> str:
        assert not self._closed, "transcribe_final() after close()"
        self._audio_ended.set()
        await self._worker
        return await self._transcribe(bytes(self._audio), self._final_beam_size, "final")

    async def close(self) -> None:
        self._closed = True
        self._audio_ended.set()
        if not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._end_stream()  # also when the worker was cancelled before it ever ran

    def _end_stream(self) -> None:
        if not self._stream_ended:
            self._stream_ended = True
            self._events.put_nowait(TextEnd())

    async def _partial_worker(self) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._audio_ended.wait(), timeout=self._partial_interval_seconds
                    )
                    return  # audio ended: the final transcript replaces further partials
                except TimeoutError:
                    pass
                end_byte = len(self._audio)
                if end_byte == 0:
                    continue
                start_byte = max(0, end_byte - self._partial_window_bytes)
                audio = bytes(self._audio[start_byte:end_byte])
                text = await self._transcribe(audio, self._partial_beam_size, "partial")
                backlog = audio_seconds(len(self._audio) - end_byte)
                if backlog > PARTIAL_MAX_BACKLOG_SECONDS:
                    self._logger.debug("dropping stale partial backlog_seconds=%.2f", backlog)
                    continue
                if text and text != self._last_partial_text:
                    self._last_partial_text = text
                    self._events.put_nowait(
                        TextPartial(text, audio_seconds(start_byte), audio_seconds(end_byte))
                    )
        finally:
            self._end_stream()

    async def _transcribe(self, pcm: bytes, beam_size: int, kind: str) -> str:
        started_at = time.monotonic()
        raw = await asyncio.to_thread(self._transcriber.transcribe, pcm_to_float(pcm), beam_size)
        text = self._preprocessor.preprocess(raw.strip())
        self._logger.debug("STT %s transcript text=%r", kind, text)
        self._logger.debug(
            "STT %s done audio_seconds=%.2f beam_size=%s duration_seconds=%.2f",
            kind,
            audio_seconds(len(pcm)),
            beam_size,
            time.monotonic() - started_at,
        )
        return text
