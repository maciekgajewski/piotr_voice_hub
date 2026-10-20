"""Per-satellite session (design.md §4.1 steps 1-6): arm, wake candidate, accept or cancel,
then a reply (`speak` or `release`, T-017) or the reply timeout, then re-arm. Not yet: follow-up
(T-020), idle announcements and `stop` (T-019), `play` (T-021), the speaker hint (T-018).

One `SatelliteSession.run()` task per satellite. It is the driver's only command sender and
reads the driver's events in order, so everything about speech segment N is emitted before
anything about segment N+1. `MicrophoneUnavailable` re-arms after a backoff; any other
exception, including a protocol invariant failure, propagates (fail loudly, design.md §9).

`speak` and `release` are called by the API task (T-017). They only check the state and hand the
request to `run()` through a one-shot future, so `run()` stays the only command sender. Every
`ack`ed `speak` ends with exactly one `playback_finished` while the hub runs, also when TTS or the
satellite fails (T-017 plan), like every `wake` ends with `transcript_final` or `wake_cancelled`.

A segment that is the wake word alone ("Ryszardzie", then a pause) keeps the wake open: the next
segment, if it starts within `WAKE_PAUSE_SECONDS`, is the request (T-017 fix, design.md §4.1
step 4). Otherwise the wake is cancelled and the timeout cue plays.
"""

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.hub.commands import CommandError, ErrorCode
from piotr_voice_hub.hub.events import (
    HubEvent,
    PlaybackFinished,
    SatelliteState,
    SatelliteStatus,
    TranscriptFinal,
    TranscriptPartial,
    Wake,
    WakeCancelled,
)
from piotr_voice_hub.hub.wake import match_wake_phrase, request_text
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
    PlaybackBegin,
    PlaybackChunk,
    PlaybackEnd,
    PlayCue,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
    VisualState,
)
from piotr_voice_hub.satellites.messages import PlaybackFinished as DriverPlaybackFinished
from piotr_voice_hub.satellites.protocol import MicrophoneProtocolState
from piotr_voice_hub.stt.whisper import StreamingTextEvent, TextEnd
from piotr_voice_hub.tts.interfaces import TextToSpeech, TtsError

BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
EVENT_TIMEOUT_SECONDS = 10.0  # for an event the driver owes us (started, stopped, cue)
SHUTDOWN_TIMEOUT_SECONDS = 3.0  # stop listening (waits for the stop ack) and go idle
SEGMENT_MARGIN_SECONDS = 0.1  # the driver ends a segment on a whole 32 ms frame
BYTES_PER_MS = 32  # 16 kHz, 16-bit mono
# After a segment that is the wake word alone: how long the next segment may take to start and
# still be the request. The old project gave speech 3 s to start after its device wake word
# (`initial_silence_seconds`, piotr_the_ai_server ai_server/config.py:30).
WAKE_PAUSE_SECONDS = 3.0
# One deadline for the TTS reads of a speak: opening the synthesis and every chunk read. The
# satellite commands between them (PlaybackBegin, chunks) are outside it, bounded by the
# driver, so the cap never interrupts one (PR #22 review S1). 1000 characters (the cap) take
# about 1 s on the GPU; past this, what was produced is played and the rest dropped.
PLAYBACK_PRODUCE_TIMEOUT_SECONDS = 30.0
# PlaybackEnd -> PlaybackFinished: above the driver's own drain bounds (T-014: about
# 2 x the audio + 35 s); past this the driver is treated as unavailable.
PLAYBACK_FINISHED_MARGIN_SECONDS = 60.0

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


class _ProduceTimeout(Exception):
    """The speak's TTS deadline passed (PLAYBACK_PRODUCE_TIMEOUT_SECONDS)."""


async def _tts_read[T](deadline: float, read: Awaitable[T]) -> T:
    """One TTS read against the speak's deadline; only the read is cancelled when it passes."""
    timeout = asyncio.timeout_at(deadline)
    try:
        async with timeout:
            return await read
    except TimeoutError:
        if timeout.expired():
            raise _ProduceTimeout from None
        raise


@dataclasses.dataclass(frozen=True)
class _Speak:
    playback_id: str
    text: str
    volume: float | None  # None: the satellite's volume is not touched


@dataclasses.dataclass(frozen=True)
class _Release:
    pass


@dataclasses.dataclass(frozen=True)
class _BareWake:
    """A segment that was the wake word alone; its `wake` is still open."""

    utterance_id: str  # the wake's; transcript_final and partials of the request carry it
    raw_text: str
    audio_bytes: int


@dataclasses.dataclass
class _Segment:
    utterance_id: str
    stt: SttSession
    partials: "asyncio.Task[None] | None" = None
    audio_bytes: int = 0
    candidate: bool = False  # a partial matched: `wake` sent, LISTENING visual set
    follows: _BareWake | None = None  # set if this segment may be the request after it


class SatelliteSession:
    def __init__(
        self,
        config: SatelliteConfig,
        microphone: Microphone,
        stt: Stt,
        emit: Emit,
        sleep: Sleep = asyncio.sleep,
        *,
        tts: TextToSpeech,
    ) -> None:
        self._config = config
        self._microphone = microphone
        self._stt = stt
        self._tts = tts
        self._emit = emit
        self._sleep = sleep
        self._max_audio_seconds = config.max_segment_seconds + SEGMENT_MARGIN_SECONDS
        self._protocol = MicrophoneProtocolState()
        self._commands = asyncio.Lock()
        self._listen_id: str | None = None
        self._segment: _Segment | None = None  # owned until finished or abandoned
        self._open_wake: str | None = None  # `wake` sent, no transcript_final/wake_cancelled yet
        self._bare_wake: _BareWake | None = None  # the wake word alone; waiting for the request
        self._reply: asyncio.Future[_Speak | _Release] | None = None  # set while one is awaited
        self._open_playback: str | None = None  # `speak` acked, no playback_finished yet
        self._last_status: SatelliteStatus | None = None
        self._running = False  # run() is running; shutdown() needs it stopped
        self._logger = logging.getLogger(f"{__name__}[{config.id}]")

    async def run(self) -> None:
        """Run until cancelled."""
        assert not self._running, "run() is already running"
        self._running = True
        try:
            self._status(False, SatelliteState.ERROR)
            backoff = BACKOFF_FIRST_SECONDS
            while True:
                try:
                    await self._arm()
                    backoff = BACKOFF_FIRST_SECONDS
                    if await self._listen_until_accepted():
                        await self._wait_for_reply()
                except MicrophoneUnavailable as error:
                    self._logger.warning("unavailable: %s; re-arming in %g s", error, backoff)
                    await self._recover()
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
                finally:
                    await self._abandon_segment()  # on cancellation or an unexpected error
        finally:
            self._running = False

    async def shutdown(self) -> None:
        """Clean stop on exit, after `run()` was cancelled (T-012 main calls it): best effort
        and bounded, stop listening if armed and show idle, then close the driver. A satellite
        that is offline is only closed, never reconnected for this. Emits no events."""
        assert not self._running, "cancel run() and wait for it before shutdown()"
        listen_id, self._listen_id = self._listen_id, None
        connected = self._last_status is not None and self._last_status.connected
        # Catching Exception swallows AssertionError on purpose: this is best effort on the way
        # out, so an invariant failure here (e.g. a command the protocol state left by the
        # cancelled run() refuses) is logged, and close() and the other satellites' shutdown
        # still run. The process is exiting anyway (design.md §9).
        try:
            if connected:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_SECONDS):
                    if listen_id is not None:
                        await self._command(StopListening(listen_id, "shutdown"))
                    await self._command(SetVisualState(VisualState.IDLE))
        except Exception as error:  # TimeoutError, MicrophoneUnavailable, AssertionError
            self._logger.warning("shutdown: could not stop listening and go idle: %r", error)
        finally:
            await self._microphone.close()
        self._logger.info("shut down")

    # Commands (T-017), called from the API task

    def speak(self, text: str, volume: float | None) -> str:
        """Accept a reply to speak; the playback_id. CommandError if it cannot be taken now."""
        playback_id = new_id("playback")
        self._take_reply(_Speak(playback_id, text, volume))
        self._open_playback = playback_id
        return playback_id

    def release(self) -> None:
        """No reply: re-arm without the timeout cue. CommandError if not waiting for one."""
        self._take_reply(_Release())

    def _take_reply(self, reply: _Speak | _Release) -> None:
        if self._last_status is None or not self._last_status.connected:
            raise CommandError(ErrorCode.SATELLITE_OFFLINE, f"{self._config.id} is offline")
        if self._reply is not None and self._reply.done():
            # Taken by an earlier command that run() has not picked up yet (PR #21 review N1).
            raise CommandError(ErrorCode.BUSY, f"{self._config.id} already has a reply")
        if self._reply is None:
            state = self._last_status.state.value
            raise CommandError(ErrorCode.BUSY, f"{self._config.id} is {state}, not processing")
        self._reply.set_result(reply)

    # Steps

    async def _arm(self) -> None:
        listen_id = new_id("listen")
        await self._command(SetVisualState(VisualState.IDLE))
        await self._command(StartListening(listen_id))
        self._listen_id = listen_id
        await self._expect(ListeningStarted(listen_id))
        self._logger.info("listening listen_id=%s", listen_id)
        self._status(True, SatelliteState.IDLE)

    async def _listen_until_accepted(self) -> bool:
        """True once an utterance is accepted; False if the wake word came alone and no request
        followed (disarmed, the timeout cue played)."""
        while True:
            if self._bare_wake is not None and self._segment is None:
                try:
                    async with asyncio.timeout(WAKE_PAUSE_SECONDS):
                        event = await self._event()  # wait_for_event is cancel-safe
                except TimeoutError:
                    self._logger.info(
                        "no request within %g s after the wake word", WAKE_PAUSE_SECONDS
                    )
                    await self._no_request()
                    return False
            else:
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
                outcome = await self._finish_segment(segment, event.reason)
                if outcome is not None:
                    return outcome
            else:
                raise AssertionError(f"unexpected event while listening: {event}")

    def _start_segment(self, utterance_id: str) -> None:
        assert self._segment is None, f"segment {self._segment.utterance_id} is still open"
        stt = self._stt.create_streaming_session(utterance_id, self._max_audio_seconds)
        segment = _Segment(utterance_id, stt, follows=self._bare_wake)
        segment.candidate = segment.follows is not None  # `wake` sent, LISTENING shown
        segment.partials = asyncio.create_task(self._read_partials(segment))
        self._segment = segment

    async def _read_partials(self, segment: _Segment) -> None:
        while not isinstance(event := await segment.stt.receive_text(), TextEnd):
            match = match_wake_phrase(event.text)
            if segment.follows is not None:  # the request after the wake word alone
                text = match.remainder if match is not None else request_text(event.text)
                utterance_id = segment.follows.utterance_id
            elif match is None:
                continue
            else:
                if not segment.candidate:
                    await self._raise_candidate(segment)
                text, utterance_id = match.remainder, segment.utterance_id
            if text:
                self._emit(TranscriptPartial(self._config.id, utterance_id, text))

    async def _raise_candidate(self, segment: _Segment) -> None:
        segment.candidate = True
        self._wake(segment.utterance_id)
        await self._command(SetVisualState(VisualState.LISTENING))
        self._status(True, SatelliteState.LISTENING)

    async def _finish_segment(self, segment: _Segment, reason: str) -> bool | None:
        """Take the final. True: accepted. False: no request after the wake word alone
        (disarmed, the timeout cue played). None: keep listening.

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
        follows, self._bare_wake = segment.follows, None
        if match is not None and match.remainder:
            text = match.remainder
        elif match is None and follows is not None:
            text = request_text(raw_text)
        else:
            text = ""
        self._logger.info(
            "segment utterance_id=%s reason=%s audio_ms=%d candidate=%s wake=%s%s",
            segment.utterance_id,
            reason,
            segment.audio_bytes // BYTES_PER_MS,
            segment.candidate,
            bool(text),
            "" if follows is None else f" after the wake word of {follows.utterance_id}",
        )
        if text:
            if follows is not None:
                raw_text = f"{follows.raw_text} {raw_text}"
            await self._accept(segment, text, raw_text, follows)
            return True
        if match is not None:  # the wake word alone ("Ryszardzie", a pause): keep it open
            if not segment.candidate:
                await self._raise_candidate(segment)
            self._bare_wake = follows or _BareWake(
                segment.utterance_id, raw_text, segment.audio_bytes
            )
            self._logger.info(
                "wake word alone: waiting up to %g s for the request", WAKE_PAUSE_SECONDS
            )
            return None
        if follows is not None:  # no request after the wake word alone (silence, noise)
            await self._no_request()
            return False
        if segment.candidate:
            assert self._listen_id is not None
            self._cancel_wake()
            await self._command(ResetWakeCandidate(self._listen_id, segment.utterance_id))
            await self._command(SetVisualState(VisualState.IDLE))
            self._status(True, SatelliteState.IDLE)
        return None

    async def _no_request(self) -> None:
        """The wake word came alone and no request followed: cancel the wake, stop listening
        and play the timeout cue, as for a reply timeout. The caller re-arms."""
        self._bare_wake = None
        listen_id, self._listen_id = self._listen_id, None
        assert listen_id is not None and self._open_wake is not None
        await self._command(ResetWakeCandidate(listen_id, self._open_wake))
        self._cancel_wake()
        await self._command(StopListening(listen_id, "no_request"))
        await self._expect(ListeningStopped(listen_id, "no_request"))
        await self._play_cue(CueType.TIMEOUT)

    async def _accept(
        self, segment: _Segment, text: str, raw_text: str, follows: _BareWake | None
    ) -> None:
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
        audio_bytes = segment.audio_bytes + (0 if follows is None else follows.audio_bytes)
        self._emit(
            TranscriptFinal(
                self._config.id,
                segment.utterance_id if follows is None else follows.utterance_id,
                text=text,
                raw_text=raw_text,
                follow_up=False,
                speaker=None,
                audio_ms=audio_bytes // BYTES_PER_MS,
            )
        )

    async def _wait_for_reply(self) -> None:
        """Wait for `speak` or `release`, at most the reply timeout. Reading events meanwhile
        notices a disconnect at once."""
        reply = self._reply = asyncio.get_running_loop().create_future()
        reader = asyncio.create_task(self._event())
        try:
            await asyncio.wait(
                {reply, reader},
                timeout=self._config.reply_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            self._reply = None
            reply.cancel()  # no-op once a command set it; later commands are busy
            reader.cancel()
            await asyncio.wait({reader})  # wait_for_event is cancel-safe
        if not reader.cancelled():
            if (error := reader.exception()) is not None:
                raise error  # MicrophoneUnavailable: an acked speak ends in _recover
            raise AssertionError(f"unexpected event while waiting for a reply: {reader.result()}")
        if reply.cancelled():
            self._logger.info("reply timeout after %g s", self._config.reply_timeout_seconds)
            await self._play_cue(CueType.TIMEOUT)
            return
        request = reply.result()
        if isinstance(request, _Release):
            self._logger.info("released")
            return
        await self._speak(request)

    async def _speak(self, request: _Speak) -> None:
        """`PlaybackBegin` goes out with the first chunk, so a synthesis that fails before any
        audio is no playback at all: the timeout cue plays instead, so the person does not
        hear only silence after the accepted cue (T-017 follow-up). Either way the acked speak
        ends with one `playback_finished`."""
        playback_id = request.playback_id
        self._status(True, SatelliteState.SPEAKING)
        self._logger.info(
            "speaking playback_id=%s (%d characters, volume %s)",
            playback_id,
            len(request.text),
            "unchanged" if request.volume is None else f"{request.volume:g}",
        )
        began = False
        audio_bytes = 0
        byte_rate = 1
        deadline = asyncio.get_running_loop().time() + PLAYBACK_PRODUCE_TIMEOUT_SECONDS
        try:
            async with contextlib.AsyncExitStack() as stack:
                synthesis = stack.enter_async_context(self._tts.synthesize(request.text))
                speech = await _tts_read(deadline, synthesis)
                audio = speech.format
                byte_rate = audio.rate * audio.width * audio.channels
                # Closed before the synthesis (PR #23 review N1), also on a satellite failure.
                chunks = await stack.enter_async_context(contextlib.aclosing(aiter(speech)))
                while (chunk := await _tts_read(deadline, anext(chunks, None))) is not None:
                    if not began:
                        await self._command(
                            PlaybackBegin(
                                playback_id, audio.rate, audio.width, audio.channels, request.volume
                            )
                        )
                        began = True
                    await self._command(PlaybackChunk(playback_id, chunk))
                    audio_bytes += len(chunk)
        except TtsError as error:
            self._logger.error(
                "speak playback_id=%s: TTS failed: %s; %s",
                playback_id,
                error,
                "playing what was produced" if began else "no audio: playing the timeout cue",
            )
        except _ProduceTimeout:
            self._logger.error(
                "speak playback_id=%s: audio not complete within %g s; %s",
                playback_id,
                PLAYBACK_PRODUCE_TIMEOUT_SECONDS,
                "playing what was produced" if began else "no audio: playing the timeout cue",
            )
        if began:
            audio_seconds = audio_bytes / byte_rate
            await self._command(PlaybackEnd(playback_id))
            await self._expect(
                DriverPlaybackFinished(playback_id),
                2 * audio_seconds + PLAYBACK_FINISHED_MARGIN_SECONDS,
            )
            self._logger.info("played playback_id=%s (%.1f s)", playback_id, audio_seconds)
        else:
            await self._play_cue(CueType.TIMEOUT)
        self._finish_playback()

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

    async def _expect(self, expected: MicrophoneEvent, timeout: float | None = None) -> None:
        timeout = EVENT_TIMEOUT_SECONDS if timeout is None else timeout
        try:
            async with asyncio.timeout(timeout):
                event = await self._event()
        except TimeoutError as error:
            raise MicrophoneUnavailable(
                f"no {type(expected).__name__} within {timeout:g} s"
            ) from error
        assert event == expected, f"expected {expected}, got {event}"

    async def _recover(self) -> None:
        self._listen_id = None
        self._bare_wake = None
        await self._abandon_segment()
        self._cancel_wake()  # every `wake` ends with transcript_final or wake_cancelled
        self._finish_playback()  # every acked `speak` ends with playback_finished
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

    def _finish_playback(self) -> None:
        if self._open_playback is not None:
            self._emit(PlaybackFinished(self._config.id, self._open_playback, False))
            self._open_playback = None

    def _status(self, connected: bool, state: SatelliteState) -> None:
        status = SatelliteStatus(self._config.id, self._config.area, connected, state)
        if status != self._last_status:  # not once per re-arm attempt
            self._last_status = status
            self._emit(status)
