"""ESPHome native-API satellite driver: connection and capture (design.md §4.1, §5).

Copied from piotr_the_ai_server ai_server/microphones/drivers/box3_esphome.py @ a299643 and
adapted to the hub: continuous (open-mic) listening only, Silero VAD instead of the peak gate,
a 1 s pre-roll and a maximum segment length. Kept from the old driver: the stop acknowledgement
(MP-STOP-001), dropping capture events of a stopped generation (MP-STOP-002), and transport
recovery (a failure makes the driver unavailable until the session closes and re-arms it, which
reconnects). Playback is T-014; its commands fail loudly until then.

Every await on the device has a timeout. Failures raise `MicrophoneUnavailable` from
`wait_for_event()`/`send_output_event()`; the session then calls `close()` and starts again,
with backoff (T-010). `close()` also wakes a reader blocked in `wait_for_event()`.

`ListeningStarted` is sent only after the device has started its voice-assistant stream: a
service call is fire-and-forget in aioesphomeapi, and a muted or busy device may never start.

`SpeechEnded.reason` is `completed` (the VAD heard the end-of-speech silence) or `max_length`
(the segment reached `max_segment_seconds`, counted including the ~1 s pre-roll).
"""

import asyncio
import logging
import math
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast

from aioesphomeapi.client import APIClient
from aioesphomeapi.core import APIConnectionError
from aioesphomeapi.model import VoiceAssistantEventType

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.ids import new_id
from piotr_voice_hub.satellites.interfaces import MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    Close,
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
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget
from piotr_voice_hub.satellites.vad import FRAME_BYTES, FRAME_SECONDS, VadTransition

API_PORT = 6053
CLIENT_INFO = "piotr-voice-hub"
CONNECT_TIMEOUT_SECONDS = 10.0
# Longer than the BOX-3 firmware's 5 s wait for its speaker before it starts the stream.
START_TIMEOUT_SECONDS = 8.0
SERVICE_TIMEOUT_SECONDS = 5.0
DISCONNECT_TIMEOUT_SECONDS = 5.0
STOP_ACK_TIMEOUT_SECONDS = 2.0
PRE_ROLL_SECONDS = 1.0
# About four maximum-length segments of 32 ms chunks. A reader this far behind is stuck, and the
# driver fails loudly rather than grow without bound or drop audio silently.
MAX_QUEUED_EVENTS = 2048
CAPTURE_RATE = 16000
CAPTURE_WIDTH = 2
CAPTURE_CHANNELS = 1

START_OPEN_MIC_LISTENING_SERVICE = "start_open_mic_listening"
STOP_LISTENING_SERVICE = "stop_listening"
RESET_OPEN_MIC_WAKE_CANDIDATE_SERVICE = "reset_open_mic_wake_candidate"
VISUAL_STATE_SERVICES = {
    VisualState.IDLE: "set_visual_idle",
    VisualState.LISTENING: "set_visual_listening",
    VisualState.PROCESSING: "set_visual_processing",
}
CUE_SERVICES = {
    CueType.UTTERANCE_ACCEPTED: "play_message_end_cue",
    CueType.TIMEOUT: "play_conversation_timeout_cue",
    CueType.FOLLOW_UP_READY: "play_follow_up_ready_cue",  # added by the P1 firmware work
}
REQUIRED_SERVICES = (
    START_OPEN_MIC_LISTENING_SERVICE,
    STOP_LISTENING_SERVICE,
    *VISUAL_STATE_SERVICES.values(),
    CUE_SERVICES[CueType.UTTERANCE_ACCEPTED],
    CUE_SERVICES[CueType.TIMEOUT],
)
# Logged at connect when missing; the command is then skipped with a warning, never a crash.
OPTIONAL_SERVICES = (RESET_OPEN_MIC_WAKE_CANDIDATE_SERVICE, CUE_SERVICES[CueType.FOLLOW_UP_READY])
CAPTURE_EVENT_TYPES = (SpeechStarted, AudioChunk, SpeechEnded)
EXPECTED_ERRORS = (APIConnectionError, OSError, TimeoutError)


class Vad(Protocol):
    """What the driver needs from `SileroVad` (a fake in unit tests)."""

    def process_frame(self, pcm: bytes) -> tuple[float, VadTransition | None]: ...

    def reset(self) -> None: ...

    def force_end(self) -> None: ...


type OnStop = Callable[[bool], Coroutine[Any, Any, None]]


class EsphomeClient(Protocol):
    """The part of `aioesphomeapi.APIClient` the driver uses (a fake in unit tests)."""

    async def connect(self, on_stop: OnStop | None = None, login: bool = False) -> None: ...

    async def disconnect(self, force: bool = False) -> None: ...

    async def list_entities_services(self) -> tuple[list[Any], list[Any]]: ...

    async def execute_service(self, service: Any, data: dict[str, Any]) -> Any: ...

    def subscribe_voice_assistant(
        self,
        *,
        handle_start: Callable[[str, int, Any, str | None], Coroutine[Any, Any, int | None]],
        handle_stop: Callable[[bool], Coroutine[Any, Any, None]],
        handle_audio: Callable[[bytes, bytes | None], Coroutine[Any, Any, None]],
    ) -> Callable[[], None]: ...

    def send_voice_assistant_event(self, event_type: Any, data: dict[str, str] | None) -> None: ...


type ClientFactory = Callable[[PlaybackTarget], EsphomeClient]


def create_api_client(target: PlaybackTarget) -> EsphomeClient:
    client = APIClient(
        target.address, API_PORT, password=None, client_info=CLIENT_INFO, noise_psk=target.api_key
    )
    return cast(EsphomeClient, client)


@dataclass(frozen=True)
class _Wake:
    """Queued instead of an event: the reader must raise MicrophoneUnavailable(reason)."""

    reason: str


class EsphomeSatellite:
    """One Voice PE / S3-BOX-3 satellite. Implements `Microphone` (capture, cues, visuals)."""

    def __init__(
        self,
        config: SatelliteConfig,
        vad: Vad,
        client_factory: ClientFactory = create_api_client,
    ) -> None:
        self.context = MicrophoneContext("esphome", config.id, config.area)
        self.playback_target = PlaybackTarget("esphome", config.id, config.address, config.api_key)
        self._logger = logging.getLogger(f"{__name__}[{config.id}]")
        self._vad = vad
        self._client_factory = client_factory
        self._max_segment_frames = math.ceil(round(config.max_segment_seconds / FRAME_SECONDS, 6))
        self._pre_roll: deque[bytes] = deque(maxlen=int(PRE_ROLL_SECONDS / FRAME_SECONDS))
        self._events: asyncio.Queue[MicrophoneEvent | _Wake] = asyncio.Queue(MAX_QUEUED_EVENTS)
        self._readers = 0  # tasks blocked in wait_for_event()
        self._connect_lock = asyncio.Lock()
        self._client: EsphomeClient | None = None  # published only once fully connected
        self._unsubscribe: Callable[[], None] | None = None
        self._services: dict[str, Any] = {}
        self._unavailable: str | None = None  # set on failure; cleared by close()
        self._listen_id: str | None = None
        self._armed = False  # capture events are accepted only while armed
        self._utterance_id: str | None = None
        self._segment_frames = 0
        self._frame_buffer = bytearray()
        self._run_active = False  # the device is streaming a voice-assistant run for us
        self._stream_started = asyncio.Event()
        self._stream_stopped = asyncio.Event()
        self._stale_stop: asyncio.Task[None] | None = None
        self._dropped_chunks = 0

    # Microphone interface

    async def wait_for_event(self) -> MicrophoneEvent:
        await self._ensure_connected()
        self._readers += 1
        try:
            item = await self._events.get()
        finally:
            self._readers -= 1
        if isinstance(item, _Wake):
            raise MicrophoneUnavailable(item.reason)
        self._logger.debug("event %s %s", item, self._state())
        return item

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self._logger.debug("command %s %s", event, self._state())
        if isinstance(event, Close):
            await self.close()
            return
        if isinstance(event, PlaybackBegin | PlaybackChunk | PlaybackEnd):
            raise NotImplementedError("ESPHome playback is not implemented yet (T-014)")
        await self._ensure_connected()
        if isinstance(event, StartListening):
            await self._start_listening(event.listen_id)
        elif isinstance(event, StopListening):
            await self._stop_listening(event.listen_id, event.reason)
        elif isinstance(event, SetVisualState):
            await self._call(VISUAL_STATE_SERVICES[event.state])
        elif isinstance(event, ResetWakeCandidate):
            assert event.listen_id == self._listen_id, (event.listen_id, self._listen_id)
            await self._call_optional(RESET_OPEN_MIC_WAKE_CANDIDATE_SERVICE)
        elif isinstance(event, PlayCue):
            assert self._listen_id is None, "cues play only while disarmed (MP-AUDIO-001)"
            # Fire-and-forget: the cue may still be playing (T-014: a supports_response service).
            if await self._call_optional(CUE_SERVICES[event.cue_type]):
                self._logger.info("played cue %s cue_id=%s", event.cue_type.value, event.cue_id)
            self._emit(CueFinished(event.cue_id))

    async def close(self) -> None:
        """Disconnect and forget all state; the next call reconnects (session recovery)."""
        if self._stale_stop is not None:
            self._stale_stop.cancel()
            self._stale_stop = None
        await self._drop_client()
        self._unavailable = None
        self._listen_id = None
        self._armed = False
        self._reset_capture()
        self._run_active = False
        self._drain_events()
        for _ in range(self._readers):
            self._events.put_nowait(_Wake("driver closed"))
        self._logger.info("closed")

    # Connection

    async def _ensure_connected(self) -> None:
        if self._unavailable is None and self._client is None:
            async with self._connect_lock:  # the reader and a command may both connect first
                if self._unavailable is None and self._client is None:
                    await self._connect()
        if self._unavailable is not None:
            raise MicrophoneUnavailable(self._unavailable)

    async def _connect(self) -> None:
        client = self._client_factory(self.playback_target)
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                await client.connect(on_stop=partial(self._on_transport_stop, client), login=True)
                _, services = await client.list_entities_services()
        except asyncio.CancelledError:
            await self._disconnect(client)  # never leave a half-open connection behind
            raise
        except EXPECTED_ERRORS as error:
            self._logger.warning("connect to %s failed: %r", self._address(), error)
            await self._disconnect(client)
            raise MicrophoneUnavailable(
                f"connect to {self._address()} failed: {error!r}"
            ) from error
        names = {service.name for service in services}
        if missing := [name for name in REQUIRED_SERVICES if name not in names]:
            await self._disconnect(client)
            raise RuntimeError(f"ESPHome firmware at {self._address()} lacks services {missing}")
        if absent := [name for name in OPTIONAL_SERVICES if name not in names]:
            self._logger.warning("firmware lacks optional services %s", absent)
        self._services = {service.name: service for service in services}
        self._unsubscribe = client.subscribe_voice_assistant(
            handle_start=self._handle_start,
            handle_stop=self._handle_stop,
            handle_audio=self._handle_audio,
        )
        self._client = client  # last: no caller ever sees a half-connected client
        self._logger.info("connected to %s", self._address())

    async def _on_transport_stop(self, client: EsphomeClient, expected_disconnect: bool) -> None:
        if client is not self._client:
            return  # an old, already replaced connection
        self._client = None
        self._unsubscribe = None
        self._fail(f"ESPHome transport stopped (expected={expected_disconnect})")

    async def _drop_client(self) -> None:
        client, unsubscribe = self._client, self._unsubscribe
        self._client, self._unsubscribe, self._services = None, None, {}
        if unsubscribe is not None:
            unsubscribe()
        if client is not None:
            await self._disconnect(client)

    async def _disconnect(self, client: EsphomeClient) -> None:
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT_SECONDS):
                await client.disconnect()
        except EXPECTED_ERRORS:
            self._logger.warning("graceful disconnect failed; forcing it", exc_info=True)
            try:
                async with asyncio.timeout(DISCONNECT_TIMEOUT_SECONDS):
                    await client.disconnect(force=True)
            except EXPECTED_ERRORS:
                self._logger.warning("forced disconnect failed", exc_info=True)

    def _fail(self, reason: str) -> None:
        """Make the driver unavailable: drop queued events and wake the reader."""
        if self._unavailable is not None:
            return
        self._logger.warning("unavailable: %s %s", reason, self._state())
        self._unavailable = reason
        self._armed = False
        self._drain_events()
        self._events.put_nowait(_Wake(reason))
        # Wake a pending start or stop-ack wait now; it re-checks _unavailable.
        self._stream_started.set()
        self._stream_stopped.set()

    async def _lose_transport(self, reason: str) -> MicrophoneUnavailable:
        self._fail(reason)
        await self._drop_client()
        return MicrophoneUnavailable(reason)

    async def _call(self, name: str) -> None:
        service = self._services.get(name)
        if service is None:
            raise RuntimeError(f"ESPHome firmware has no {name} service")
        client = self._client
        if client is None:
            raise MicrophoneUnavailable(self._unavailable or "not connected")
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT_SECONDS):
                await client.execute_service(service, {})
        except EXPECTED_ERRORS as error:
            raise await self._lose_transport(f"service {name} failed: {error!r}") from error
        self._logger.debug("executed service %s", name)

    async def _call_optional(self, name: str) -> bool:
        if name not in self._services:
            self._logger.warning("firmware has no %s service; skipped", name)
            return False
        await self._call(name)
        return True

    # Listening

    async def _start_listening(self, listen_id: str) -> None:
        assert self._listen_id is None, "StartListening while armed"
        if self._stale_stop is not None:
            await self._stale_stop  # bounded by its own timeouts
            self._stale_stop = None
            if self._unavailable is not None:
                raise MicrophoneUnavailable(self._unavailable)
        self._drain_events()
        self._reset_capture()
        if self._dropped_chunks:
            self._logger.debug("dropped %s audio chunks while disarmed", self._dropped_chunks)
            self._dropped_chunks = 0
        self._listen_id, self._armed = listen_id, True
        self._stream_started.clear()
        await self._call(START_OPEN_MIC_LISTENING_SERVICE)
        try:
            async with asyncio.timeout(START_TIMEOUT_SECONDS):
                await self._stream_started.wait()
        except TimeoutError as error:
            raise await self._lose_transport(
                f"device did not start streaming within {START_TIMEOUT_SECONDS} s "
                "(muted, busy or wrong firmware?)"
            ) from error
        if self._unavailable is not None:  # woken by _fail(): disconnected while starting
            raise MicrophoneUnavailable(self._unavailable)
        self._logger.info("listening listen_id=%s", listen_id)
        self._emit(ListeningStarted(listen_id))

    async def _stop_listening(self, listen_id: str, reason: str) -> None:
        assert listen_id == self._listen_id, (listen_id, self._listen_id)
        self._armed = False
        self._drop_capture_events()  # MP-STOP-002
        self._reset_capture()
        if self._run_active:  # always true after a confirmed start, unless the driver failed
            await self._stop_device_stream("stop")
        self._listen_id = None
        self._logger.info("stopped listening listen_id=%s reason=%s", listen_id, reason)
        self._emit(ListeningStopped(listen_id, reason))

    async def _stop_device_stream(self, what: str) -> None:
        """MP-STOP-001: stop the device pipeline and wait for its own stop notification."""
        self._stream_stopped.clear()
        await self._call(STOP_LISTENING_SERVICE)
        try:
            async with asyncio.timeout(STOP_ACK_TIMEOUT_SECONDS):
                await self._stream_stopped.wait()
        except TimeoutError as error:
            raise await self._lose_transport(
                f"no {what} acknowledgement within {STOP_ACK_TIMEOUT_SECONDS} s"
            ) from error
        if self._unavailable is not None:  # woken by _fail(): disconnected while stopping
            raise MicrophoneUnavailable(self._unavailable)
        # A failure here marks the driver unavailable; the reader then gets that, not the
        # ListeningStopped the caller emits (_emit drops events once unavailable).
        self._send_run_end()

    async def _stop_stale_stream(self) -> None:
        self._logger.info("stopping a device stream that started while disarmed")
        try:
            await self._stop_device_stream("stale-stream stop")
        except (MicrophoneUnavailable, RuntimeError) as error:
            self._fail(f"stopping a stale device stream failed: {error}")

    def _send_run_end(self) -> None:
        if self._client is None:
            return
        try:
            self._client.send_voice_assistant_event(
                VoiceAssistantEventType.VOICE_ASSISTANT_RUN_END, None
            )
        except EXPECTED_ERRORS as error:
            self._fail(f"sending run end failed: {error!r}")

    # Device callbacks

    async def _handle_start(
        self, conversation_id: str, flags: int, audio_settings: Any, wake_word: str | None
    ) -> int:
        self._logger.debug(
            "device stream start conversation_id=%s flags=%s armed=%s",
            conversation_id,
            flags,
            self._armed,
        )
        if not self._armed:
            # A start that arrives after StopListening (or with nothing armed) would stream
            # forever in continuous mode: stop it (MP-STOP-001). Its audio is dropped meanwhile.
            if self._stale_stop is None or self._stale_stop.done():
                self._stale_stop = asyncio.ensure_future(self._stop_stale_stream())
            return 0
        if self._run_active:
            self._fail("device started a second voice-assistant stream while listening")
            return 0
        self._reset_capture()
        self._run_active = True
        self._stream_started.set()
        return 0  # 0: audio comes over the API connection

    async def _handle_stop(self, aborted: bool) -> None:
        self._logger.debug("device stream stop aborted=%s armed=%s", aborted, self._armed)
        was_active, self._run_active = self._run_active, False
        self._stream_stopped.set()
        if self._armed and was_active:
            # Nobody asked it to stop: listening is dead, so fail loudly; the session re-arms.
            self._fail("device ended its voice-assistant stream while listening")

    async def _handle_audio(self, data: bytes, data2: bytes | None = None) -> None:
        for chunk in (data, data2):
            if not chunk:
                continue
            if not self._armed:
                self._dropped_chunks += 1
                if self._dropped_chunks == 1 or self._dropped_chunks % 100 == 0:
                    self._logger.debug(
                        "dropped audio while disarmed chunks=%s", self._dropped_chunks
                    )
                continue
            self._frame_buffer += chunk
            while len(self._frame_buffer) >= FRAME_BYTES and self._armed:
                frame = bytes(self._frame_buffer[:FRAME_BYTES])
                del self._frame_buffer[:FRAME_BYTES]
                self._process_frame(frame)

    # Segmentation

    def _process_frame(self, frame: bytes) -> None:
        _, transition = self._vad.process_frame(frame)
        assert self._listen_id is not None
        if self._utterance_id is None:
            self._pre_roll.append(frame)
            if transition is VadTransition.SPEECH_STARTED:
                # The start fires >= 256 ms after onset; the pre-roll holds the onset (vad.py).
                self._utterance_id = new_id("utterance")
                self._emit(
                    SpeechStarted(
                        self._listen_id,
                        self._utterance_id,
                        CAPTURE_RATE,
                        CAPTURE_WIDTH,
                        CAPTURE_CHANNELS,
                    )
                )
                self._emit(
                    AudioChunk(self._listen_id, self._utterance_id, b"".join(self._pre_roll))
                )
                self._segment_frames = len(self._pre_roll)
                self._pre_roll.clear()
            return
        self._emit(AudioChunk(self._listen_id, self._utterance_id, frame))
        self._segment_frames += 1
        if transition is VadTransition.SPEECH_ENDED:
            self._end_segment("completed")
        elif self._segment_frames >= self._max_segment_frames:
            self._vad.force_end()
            self._end_segment("max_length")

    def _end_segment(self, reason: str) -> None:
        assert self._listen_id is not None and self._utterance_id is not None
        self._emit(SpeechEnded(self._listen_id, self._utterance_id, reason))
        self._logger.debug(
            "segment ended utterance_id=%s reason=%s seconds=%.2f",
            self._utterance_id,
            reason,
            self._segment_frames * FRAME_SECONDS,
        )
        self._utterance_id = None
        self._segment_frames = 0

    def _reset_capture(self) -> None:
        self._vad.reset()
        self._pre_roll.clear()
        self._frame_buffer.clear()
        self._utterance_id = None
        self._segment_frames = 0

    # Events

    def _emit(self, event: MicrophoneEvent) -> None:
        if self._unavailable is not None:
            return
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:
            self._fail(f"event queue full ({MAX_QUEUED_EVENTS}): the reader is stuck")

    def _drain_events(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()

    def _drop_capture_events(self) -> None:
        kept: list[MicrophoneEvent | _Wake] = []
        dropped = 0
        while not self._events.empty():
            event = self._events.get_nowait()
            if isinstance(event, CAPTURE_EVENT_TYPES):
                dropped += 1
            else:
                kept.append(event)
        for event in kept:
            self._events.put_nowait(event)
        if dropped:
            self._logger.debug(
                "dropped %s queued capture events of the stopped generation", dropped
            )

    def _address(self) -> str:
        return f"{self.playback_target.address}:{API_PORT}"

    def _state(self) -> str:
        return (
            f"connected={self._client is not None} unavailable={self._unavailable is not None} "
            f"listen_id={self._listen_id} armed={self._armed} utterance_id={self._utterance_id} "
            f"run_active={self._run_active} queued={self._events.qsize()}"
        )
