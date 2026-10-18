"""Per-satellite session (design.md §4.1 steps 1-5): arm, wake candidate, accept or cancel,
reply timeout, re-arm. P0: no follow-up, busy policy, speak/play/release or speaker hint (P1).

One `SatelliteSession.run()` task per satellite. It is the driver's only command sender and
reads the driver's events in order, so everything about speech segment N is emitted before
anything about segment N+1. `MicrophoneUnavailable` re-arms after a backoff; any other
exception, including a protocol invariant failure, propagates (fail loudly, design.md §9).
"""

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.hub.events import (
    HubEvent,
    SatelliteState,
    SatelliteStatus,
    TranscriptFinal,
    TranscriptPartial,
    Wake,
    WakeCancelled,
)
from piotr_voice_hub.hub.wake import WakeMatch, match_wake_phrase
from piotr_voice_hub.ids import new_id
from piotr_voice_hub.satellites.interfaces import Microphone, MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    CueFinished,
    CueType,
    ListeningStarted,
    ListeningStopped,
    MicrophoneCommand,
    MicrophoneEvent,
    PlayCue,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
    VisualState,
)
from piotr_voice_hub.satellites.protocol import MicrophoneProtocolState
from piotr_voice_hub.stt.whisper import StreamingTextEvent, TextEnd

BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
EVENT_TIMEOUT_SECONDS = 10.0  # for an event the driver owes us (started, stopped, cue)
SHUTDOWN_TIMEOUT_SECONDS = 3.0  # stop listening (waits for the stop ack) and go idle
SEGMENT_MARGIN_SECONDS = 0.1  # the driver ends a segment on a whole 32 ms frame
BYTES_PER_MS = 32  # 16 kHz, 16-bit mono

_logger = logging.getLogger(__name__)

type Emit = Callable[[HubEvent], None]
type Sleep = Callable[[float], Awaitable[None]]


class SttSession(Protocol):
    async def send_audio(self, pcm: bytes) -> None: ...
    async def receive_text(self) -> StreamingTextEvent: ...
    async def transcribe_final(self) -> str: ...
    async def close(self) -> None: ...


class Stt(Protocol):
    def create_streaming_session(self, session_id: str, max_audio_seconds: float) -> SttSession: ...


@dataclasses.dataclass
class _Segment:
    utterance_id: str
    stt: SttSession
    partials: "asyncio.Task[None] | None" = None
    audio_bytes: int = 0
    candidate: bool = False  # a partial matched: `wake` sent, LISTENING visual set


class SatelliteSession:
    def __init__(
        self,
        config: SatelliteConfig,
        microphone: Microphone,
        stt: Stt,
        emit: Emit,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config = config
        self._microphone = microphone
        self._stt = stt
        self._emit = emit
        self._sleep = sleep
        self._max_audio_seconds = config.max_segment_seconds + SEGMENT_MARGIN_SECONDS
        self._protocol = MicrophoneProtocolState()
        self._commands = asyncio.Lock()
        self._listen_id: str | None = None
        self._segment: _Segment | None = None  # owned until finished or abandoned
        self._open_wake: str | None = None  # `wake` sent, no transcript_final/wake_cancelled yet
        self._last_status: SatelliteStatus | None = None
        self._logger = logging.getLogger(f"{__name__}[{config.id}]")

    async def run(self) -> None:
        """Run until cancelled."""
        self._status(False, SatelliteState.ERROR)
        backoff = BACKOFF_FIRST_SECONDS
        while True:
            try:
                await self._arm()
                backoff = BACKOFF_FIRST_SECONDS
                await self._listen_until_accepted()
                await self._wait_for_reply()
            except MicrophoneUnavailable as error:
                self._logger.warning("unavailable: %s; re-arming in %g s", error, backoff)
                await self._recover()
                await self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
            finally:
                await self._abandon_segment()  # on cancellation or an unexpected error

    async def shutdown(self) -> None:
        """Clean stop on exit, after `run()` was cancelled (T-012 main calls it): best effort
        and bounded, stop listening if armed and show idle, then close the driver. A satellite
        that is offline is only closed, never reconnected for this. Emits no events."""
        listen_id, self._listen_id = self._listen_id, None
        connected = self._last_status is not None and self._last_status.connected
        try:
            if connected:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_SECONDS):
                    if listen_id is not None:
                        await self._command(StopListening(listen_id, "shutdown"))
                    await self._command(SetVisualState(VisualState.IDLE))
        except Exception as error:  # includes TimeoutError and MicrophoneUnavailable
            self._logger.warning("shutdown: could not stop listening and go idle: %r", error)
        finally:
            await self._microphone.close()
        self._logger.info("shut down")

    # Steps

    async def _arm(self) -> None:
        listen_id = new_id("listen")
        await self._command(SetVisualState(VisualState.IDLE))
        await self._command(StartListening(listen_id))
        self._listen_id = listen_id
        await self._expect(ListeningStarted(listen_id))
        self._logger.info("listening listen_id=%s", listen_id)
        self._status(True, SatelliteState.IDLE)

    async def _listen_until_accepted(self) -> None:
        while True:
            event = await self._event()
            segment = self._segment
            if isinstance(event, SpeechStarted):
                self._start_segment(event.utterance_id)
            elif isinstance(event, AudioChunk):
                assert segment is not None
                segment.audio_bytes += len(event.data)
                await segment.stt.send_audio(event.data)
            elif isinstance(event, SpeechEnded):
                assert segment is not None
                if await self._finish_segment(segment, event.reason):
                    return
            else:
                raise AssertionError(f"unexpected event while listening: {event}")

    def _start_segment(self, utterance_id: str) -> None:
        assert self._segment is None, f"segment {self._segment.utterance_id} is still open"
        stt = self._stt.create_streaming_session(utterance_id, self._max_audio_seconds)
        segment = _Segment(utterance_id, stt)
        segment.partials = asyncio.create_task(self._read_partials(segment))
        self._segment = segment

    async def _read_partials(self, segment: _Segment) -> None:
        while not isinstance(event := await segment.stt.receive_text(), TextEnd):
            match = match_wake_phrase(event.text)
            if match is None:
                continue
            if not segment.candidate:
                await self._raise_candidate(segment)
            if match.remainder:
                self._emit(
                    TranscriptPartial(self._config.id, segment.utterance_id, match.remainder)
                )

    async def _raise_candidate(self, segment: _Segment) -> None:
        segment.candidate = True
        self._wake(segment.utterance_id)
        await self._command(SetVisualState(VisualState.LISTENING))
        self._status(True, SatelliteState.LISTENING)

    async def _finish_segment(self, segment: _Segment, reason: str) -> bool:
        """Take the final; return True if the utterance was accepted.

        The segment stays owned (`self._segment`) until its STT session is closed and its
        partial task has ended, so a cancellation or failure in here abandons it fully.
        A crash in the partial task surfaces here, at the end of speech."""
        raw_text = await segment.stt.transcribe_final()
        await segment.stt.close()
        assert segment.partials is not None
        await asyncio.wait({segment.partials})  # it ends at TextEnd
        if (error := segment.partials.exception()) is not None:
            raise error
        self._segment = None
        match = match_wake_phrase(raw_text)
        self._logger.info(
            "segment utterance_id=%s reason=%s audio_ms=%d candidate=%s wake=%s",
            segment.utterance_id,
            reason,
            segment.audio_bytes // BYTES_PER_MS,
            segment.candidate,
            match is not None and bool(match.remainder),
        )
        if match is not None and match.remainder:
            await self._accept(segment, match, raw_text)
            return True
        if segment.candidate:
            assert self._listen_id is not None
            self._cancel_wake()
            await self._command(ResetWakeCandidate(self._listen_id, segment.utterance_id))
            await self._command(SetVisualState(VisualState.IDLE))
            self._status(True, SatelliteState.IDLE)
        return False

    async def _accept(self, segment: _Segment, match: WakeMatch, raw_text: str) -> None:
        if not segment.candidate:
            self._wake(segment.utterance_id)
        listen_id, self._listen_id = self._listen_id, None
        assert listen_id is not None
        await self._command(StopListening(listen_id, "accepted"))
        await self._expect(ListeningStopped(listen_id, "accepted"))
        await self._play_cue(CueType.UTTERANCE_ACCEPTED)
        await self._command(SetVisualState(VisualState.PROCESSING))
        self._status(True, SatelliteState.PROCESSING)
        self._open_wake = None
        self._emit(
            TranscriptFinal(
                self._config.id,
                segment.utterance_id,
                text=match.remainder,
                raw_text=raw_text,
                follow_up=False,
                speaker=None,
                audio_ms=segment.audio_bytes // BYTES_PER_MS,
            )
        )

    async def _wait_for_reply(self) -> None:
        """P0: nothing can answer (speak/release are P1), so this always runs to the timeout.
        Reading events here notices a disconnect at once."""
        try:
            async with asyncio.timeout(self._config.reply_timeout_seconds):
                event = await self._event()
        except TimeoutError:
            self._logger.info("reply timeout after %g s", self._config.reply_timeout_seconds)
            await self._play_cue(CueType.TIMEOUT)
            return
        raise AssertionError(f"unexpected event while waiting for a reply: {event}")

    async def _play_cue(self, cue_type: CueType) -> None:
        cue_id = new_id("cue")
        await self._command(PlayCue(cue_id, cue_type))
        await self._expect(CueFinished(cue_id))

    # Driver access: every command and event is validated by the protocol state machine.

    async def _command(self, command: MicrophoneCommand) -> None:
        async with self._commands:  # the partial task also sends a visual
            self._protocol.command(command)
            await self._microphone.send_output_event(command)

    async def _event(self) -> MicrophoneEvent:
        event = await self._microphone.wait_for_event()
        self._protocol.event(event)
        return event

    async def _expect(self, expected: MicrophoneEvent) -> None:
        try:
            async with asyncio.timeout(EVENT_TIMEOUT_SECONDS):
                event = await self._event()
        except TimeoutError as error:
            raise MicrophoneUnavailable(
                f"no {type(expected).__name__} within {EVENT_TIMEOUT_SECONDS:g} s"
            ) from error
        assert event == expected, f"expected {expected}, got {event}"

    async def _recover(self) -> None:
        self._listen_id = None
        await self._abandon_segment()
        self._cancel_wake()  # every `wake` ends with transcript_final or wake_cancelled
        self._status(False, SatelliteState.ERROR)
        await self._microphone.close()
        self._protocol = MicrophoneProtocolState()  # the driver forgot everything too

    async def _abandon_segment(self) -> None:
        """Close the open segment's STT session and end its partial task. `_segment` is
        cleared only once the task has ended, also when this is cancelled part-way."""
        segment = self._segment
        if segment is None:
            return
        assert segment.partials is not None
        segment.partials.cancel()  # first: nothing below may let it send or emit more
        try:
            await segment.stt.close()
        finally:
            # asyncio.wait raises neither the task's error nor its cancellation; the task is
            # cancelled, so this is bounded by its unwinding.
            await asyncio.wait({segment.partials})
            self._segment = None
            error = None if segment.partials.cancelled() else segment.partials.exception()
            if isinstance(error, MicrophoneUnavailable):
                # The driver failed; the main loop gets (or got) the same failure.
                self._logger.warning("partial reader stopped: %s", error)
        if error is not None and not isinstance(error, MicrophoneUnavailable):
            raise error  # fail loudly, like any other unexpected error

    def _wake(self, utterance_id: str) -> None:
        self._open_wake = utterance_id
        self._emit(Wake(self._config.id, utterance_id))

    def _cancel_wake(self) -> None:
        if self._open_wake is not None:
            self._emit(WakeCancelled(self._config.id, self._open_wake))
            self._open_wake = None

    def _status(self, connected: bool, state: SatelliteState) -> None:
        status = SatelliteStatus(self._config.id, self._config.area, connected, state)
        if status != self._last_status:  # not once per re-arm attempt
            self._last_status = status
            self._emit(status)
