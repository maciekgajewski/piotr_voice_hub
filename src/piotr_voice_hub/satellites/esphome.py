"""ESPHome native-API satellite driver: connection and capture (design.md §4.1, §5).

Copied from piotr_the_ai_server ai_server/microphones/drivers/box3_esphome.py @ a299643 and
adapted to the hub: continuous (open-mic) listening only, Silero VAD instead of the peak gate,
a 1 s pre-roll and a maximum segment length. Kept from the old driver: the stop acknowledgement
(MP-STOP-001), dropping capture events of a stopped generation (MP-STOP-002), and transport
recovery (a failure makes the driver unavailable until the session closes and re-arms it, which
reconnects).

Playback (T-014, design.md §4.1 step 6): `PlaybackBegin` opens a stream on the shared playback
HTTP server and sends `media_player_command(media_url, announcement=True)`; the device fetches
`http://<hub>:<port>/<playback_id>.wav`. `PlaybackChunk` queues PCM, `PlaybackEnd` ends the
stream, and `PlaybackFinished` follows once the device has fetched all of it, plus the audio
still playing and a 0.2 s grace: the old driver's estimate. A device that never fetches the
stream, or does not fetch all of it (the GET failed or stalled mid-stream, review B1), makes the
driver unavailable instead, with no `PlaybackFinished`. Playback, like cues, happens only while
disarmed. The URL is IPv4 (the playback server is IPv4-only) and only the satellite's own IP may
fetch it. Nothing bounds the time from `PlaybackBegin` to `PlaybackEnd` here: that is the
producer's (P1 session/TTS) job.

Cues: a cue service that declares `supports_response` (firmware, P2: status mode with an
explicit `api.respond`) is called with a response, so `CueFinished` means the cue has played.
Otherwise the driver follows the media player's state (`subscribe_states`): `CueFinished` comes
once the player has reported a change after the call, at least `CUE_MIN_SECONDS` after the
call, and is not busy (`ANNOUNCING`, or `PLAYING`, which is how the API reported an
announcement on hardware), bounded (T-017 fix). It may report only the final `IDLE`: on
hardware a short cue's announcement and `IDLE` reached the API as one update, so the update
alone cannot tell the cue from a volume change; the floor lets the cue's own state arrive
before the busy check. A Voice PE silently drops an announcement URL sent while a cue is still
announcing (seen on hardware: no fetch at all), so playback also sends its URL only once the
player is not busy, bounded. These bounds log a WARNING and go on: a cue is not worth a
reconnect, and a dropped URL still ends in the 5 s fetch timeout below.

Every await on the device has a timeout. Failures raise `MicrophoneUnavailable` from
`wait_for_event()`/`send_output_event()`; the session then calls `close()` and starts again,
with backoff (T-010). `close()` also wakes a reader blocked in `wait_for_event()`.

`ListeningStarted` is sent only after the device has started its voice-assistant stream: a
service call is fire-and-forget in aioesphomeapi, and a muted or busy device may never start.

`SpeechEnded.reason` is `completed` (the VAD heard the end-of-speech silence) or `max_length`
(the segment reached `max_segment_seconds`, counted including the ~1 s pre-roll).
"""

import asyncio
import ipaddress
import logging
import math
import socket
import traceback
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast

from aioesphomeapi.client import APIClient
from aioesphomeapi.core import APIConnectionError
from aioesphomeapi.model import (
    MediaPlayerEntityState,
    MediaPlayerInfo,
    MediaPlayerState,
    SupportsResponseType,
    VoiceAssistantEventType,
)

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.hub.playback_http import PlaybackHttpServer, PlaybackStream, StreamFull
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
    PlaybackFinished,
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
# The firmware answers a status-mode cue action with `api.respond` after `script.wait`, i.e.
# once the cue sound (about 1 s) has played. 8 s is well above that and below the session's
# 10 s wait for CueFinished. ESPHome sends no response by itself: an action that declares
# `supports_response` without calling `api.respond` makes every cue wait out these 8 s and
# then drop the connection (review B2), so the firmware must do both (P2 carry-forward).
CUE_RESPONSE_TIMEOUT_SECONDS = 8.0
# A cue called without a response: the player reports a change about 40-200 ms after the call
# (voice-pe-03, T-017 fix); if it has not within this, the cue is taken as done (no sound).
CUE_START_TIMEOUT_SECONDS = 1.0
# ... and not before this after the call: an unrelated update (the dial, a stray IDLE) can come
# first, and the cue's own report is in by then (about 40-200 ms on voice-pe-03; a 0.5 s gap
# played, so the floor is that gap). If a `did not fetch playback` WARNING ever follows an
# accepted cue on hardware, raise this first.
CUE_MIN_SECONDS = 0.5
# ... and the cue has played once the player is not busy (ANNOUNCING or PLAYING). These bounds
# are the driver's own: the call (at most SERVICE_TIMEOUT_SECONDS) + 1 s + 8 s, about 14 s at
# worst, while the session holds its command lock; the session's 10 s wait for CueFinished
# starts only after the command returns, so it does not bound this.
CUE_END_TIMEOUT_SECONDS = 8.0
# Before a playback URL: wait for the player not to be busy (a cue still playing). A player
# stuck in PLAYING (nothing else plays media here) delays every speak by these 3 s with a
# WARNING; if the device then drops the URL, the 5 s fetch timeout below makes it unavailable.
PLAYER_FREE_TIMEOUT_SECONDS = 3.0
# Playback drain, from the old driver: the device must fetch the stream within 5 s of
# PlaybackEnd; sending it may then take the audio length plus 30 s; the rest of the audio
# plays out after the last byte, plus a grace.
STREAM_REQUEST_TIMEOUT_SECONDS = 5.0
STREAM_DONE_EXTRA_SECONDS = 30.0
PLAYBACK_DRAIN_GRACE_SECONDS = 0.2
VOLUME_SETTLE_SECONDS = 0.1  # the old driver's pause between the volume and the announcement
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

    @property
    def connected_address(self) -> str | None: ...

    async def execute_service(
        self,
        service: Any,
        data: dict[str, Any],
        *,
        return_response: bool | None = None,
        timeout: float = 30.0,
    ) -> Any: ...

    def media_player_command(
        self,
        key: int,
        *,
        media_url: str | None = None,
        announcement: bool | None = None,
        volume: float | None = None,
    ) -> None: ...

    def subscribe_voice_assistant(
        self,
        *,
        handle_start: Callable[[str, int, Any, str | None], Coroutine[Any, Any, int | None]],
        handle_stop: Callable[[bool], Coroutine[Any, Any, None]],
        handle_audio: Callable[[bytes, bytes | None], Coroutine[Any, Any, None]],
    ) -> Callable[[], None]: ...

    def send_voice_assistant_event(self, event_type: Any, data: dict[str, str] | None) -> None: ...

    def subscribe_states(self, on_state: Callable[[Any], None]) -> None: ...


type ClientFactory = Callable[[PlaybackTarget], EsphomeClient]
type LocalIpFor = Callable[[str], str]


def create_api_client(target: PlaybackTarget) -> EsphomeClient:
    client = APIClient(
        target.address, API_PORT, password=None, client_info=CLIENT_INFO, noise_psk=target.api_key
    )
    return cast(EsphomeClient, client)


def local_ip_for(device_ip: str) -> str:
    """The hub's IPv4 address on the route to the device (old driver): what the device can
    fetch from. A UDP connect to an IP literal sends nothing and does no DNS lookup."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((device_ip, API_PORT))
        return str(sock.getsockname()[0])


def _ipv4(address: str | None) -> str | None:
    """`address` as an IPv4 literal (also from an IPv4-mapped IPv6 one), else None."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        return None if ip.ipv4_mapped is None else str(ip.ipv4_mapped)
    return str(ip)


@dataclass(frozen=True)
class _Raise:
    """Queued instead of an event: an unexpected error in a driver task; the reader re-raises
    it, so it fails loudly in the session instead of being lost in the task."""

    error: BaseException


@dataclass(frozen=True)
class _Wake:
    """Queued instead of an event: the reader must raise MicrophoneUnavailable(reason)."""

    reason: str


@dataclass
class _Playback:
    playback_id: str
    stream: PlaybackStream
    drain: asyncio.Task[None] | None = None


class EsphomeSatellite:
    """One Voice PE / S3-BOX-3 satellite. Implements `Microphone` (capture, cues, visuals)."""

    def __init__(
        self,
        config: SatelliteConfig,
        vad: Vad,
        client_factory: ClientFactory = create_api_client,
        playback_server: PlaybackHttpServer | None = None,
        local_ip: LocalIpFor = local_ip_for,
    ) -> None:
        self.context = MicrophoneContext("esphome", config.id, config.area)
        self.playback_target = PlaybackTarget("esphome", config.id, config.address, config.api_key)
        self._logger = logging.getLogger(f"{__name__}[{config.id}]")
        self._vad = vad
        self._client_factory = client_factory
        self._playback_server = playback_server  # None: no playback (the wake-test runner)
        self._local_ip = local_ip
        self._max_segment_frames = math.ceil(round(config.max_segment_seconds / FRAME_SECONDS, 6))
        self._pre_roll: deque[bytes] = deque(maxlen=int(PRE_ROLL_SECONDS / FRAME_SECONDS))
        self._events: asyncio.Queue[MicrophoneEvent | _Wake | _Raise] = asyncio.Queue(
            MAX_QUEUED_EVENTS
        )
        self._readers = 0  # tasks blocked in wait_for_event()
        self._connect_lock = asyncio.Lock()
        self._client: EsphomeClient | None = None  # published only once fully connected
        self._unsubscribe: Callable[[], None] | None = None
        self._services: dict[str, Any] = {}
        self._media_player_key: int | None = None
        # The media player's state (subscribe_states; None until the initial one after
        # connecting), a count of later updates, and an event set on every update: cues and
        # playback wait on it (T-017 fix).
        self._player_state: MediaPlayerState | None = None
        self._player_updates = 0
        self._player_changed = asyncio.Event()
        self._device_ip: str | None = None  # IPv4 of the API connection, None if not IPv4
        self._playback: _Playback | None = None
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
        if isinstance(item, _Raise):
            raise item.error
        self._logger.debug("event %s %s", item, self._state())
        return item

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self._logger.debug("command %s %s", event, self._state())
        if isinstance(event, Close):
            await self.close()
            return
        if isinstance(event, PlaybackChunk):
            self._write_playback(event.playback_id, event.data)
            return
        if isinstance(event, PlaybackEnd):
            self._end_playback(event.playback_id)
            return
        await self._ensure_connected()
        if isinstance(event, PlaybackBegin):
            await self._begin_playback(event)
            return
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
            assert self._playback is None, "cue during playback"
            if await self._play_cue(CUE_SERVICES[event.cue_type]):
                self._logger.info("played cue %s cue_id=%s", event.cue_type.value, event.cue_id)
            self._emit(CueFinished(event.cue_id))

    async def close(self) -> None:
        """Disconnect and forget all state; the next call reconnects (session recovery)."""
        if self._stale_stop is not None:
            self._stale_stop.cancel()
            self._stale_stop = None
        self._release_playback()
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
                entities, services = await client.list_entities_services()
        except asyncio.CancelledError:
            await self._disconnect(client)  # never leave a half-open connection behind
            raise
        except EXPECTED_ERRORS as error:
            # INFO: the caller (the session) logs the one WARNING per failed attempt.
            self._logger.info("connect to %s failed: %r", self._address(), error)
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
        if no_response := [
            name
            for name in CUE_SERVICES.values()
            if name in names and self._supports_response(name) is None
        ]:
            self._logger.info(
                "cue services without supports_response (CueFinished follows the media "
                "player's announcement): %s",
                no_response,
            )
        players = [entity for entity in entities if isinstance(entity, MediaPlayerInfo)]
        self._media_player_key = players[0].key if players else None
        if not players:
            self._logger.warning("firmware exposes no media player: playback will fail")
        elif len(players) > 1:
            self._logger.warning(
                "firmware exposes %d media players; playback uses %r",
                len(players),
                players[0].name,
            )
        connected = client.connected_address
        if connected is None:  # an aioesphomeapi invariant once connected
            await self._disconnect(client)
            raise RuntimeError(f"connected to {self._address()} but no connected_address")
        self._device_ip = _ipv4(connected)
        if self._device_ip is None:
            self._logger.warning("connected over %s, not IPv4: playback will fail", connected)
        self._unsubscribe = client.subscribe_voice_assistant(
            handle_start=self._handle_start,
            handle_stop=self._handle_stop,
            handle_audio=self._handle_audio,
        )
        if self._media_player_key is not None:
            client.subscribe_states(partial(self._on_state, client))
        self._client = client  # last: no caller ever sees a half-connected client
        self._logger.info("connected to %s", self._address())

    def _on_state(self, client: EsphomeClient, state: Any) -> None:
        if client is not self._client or not isinstance(state, MediaPlayerEntityState):
            return
        if state.key != self._media_player_key:
            return
        if self._player_state is not None:  # not the initial state sent on subscribe
            self._player_updates += 1
        self._logger.debug(
            "media player %s volume=%.2f update=%d",
            getattr(state.state, "name", state.state),
            state.volume,
            self._player_updates,
        )
        self._player_state = state.state
        self._player_changed.set()

    def _player_free(self) -> bool:
        # The API reported the device's ANNOUNCING as PLAYING on hardware (T-017 fix).
        return self._player_state not in (MediaPlayerState.ANNOUNCING, MediaPlayerState.PLAYING)

    async def _wait_player(self, done: Callable[[], bool], timeout: float) -> bool:
        """Wait until `done()` holds after a media player update; False after `timeout`."""
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    if self._unavailable is not None:
                        raise MicrophoneUnavailable(self._unavailable)
                    self._player_changed.clear()
                    await self._player_changed.wait()
        except TimeoutError:
            return done()
        return True

    async def _on_transport_stop(self, client: EsphomeClient, expected_disconnect: bool) -> None:
        if client is not self._client:
            return  # an old, already replaced connection
        self._client = None
        self._unsubscribe = None
        self._fail(f"ESPHome transport stopped (expected={expected_disconnect})")

    async def _drop_client(self) -> None:
        client, unsubscribe = self._client, self._unsubscribe
        self._client, self._unsubscribe, self._services = None, None, {}
        self._media_player_key = None
        self._player_state = None
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
        self._release_playback()  # no PlaybackFinished for a playback cut short
        self._drain_events()
        self._events.put_nowait(_Wake(reason))
        # Wake a pending start or stop-ack wait now; it re-checks _unavailable.
        self._stream_started.set()
        self._stream_stopped.set()
        self._player_changed.set()

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

    def _supports_response(self, name: str) -> bool | None:
        """The `return_response` to call a service with, or None for fire-and-forget."""
        supports = getattr(self._services[name], "supports_response", None)
        if supports is None or supports == SupportsResponseType.NONE:
            return None
        return supports != SupportsResponseType.STATUS  # STATUS: success only, no data

    async def _play_cue(self, name: str) -> bool:
        """Like `_call_optional`, but waits for the firmware's response when it offers one."""
        if name not in self._services:
            self._logger.warning("firmware has no %s service; skipped", name)
            return False
        return_response = self._supports_response(name)
        if return_response is None:
            updates = self._player_updates
            called_at = asyncio.get_running_loop().time()
            await self._call(name)
            await self._wait_cue_played(name, updates, called_at)
            return True
        client = self._client
        if client is None:
            raise MicrophoneUnavailable(self._unavailable or "not connected")
        try:
            async with asyncio.timeout(CUE_RESPONSE_TIMEOUT_SECONDS + 1.0):
                response = await client.execute_service(
                    self._services[name],
                    {},
                    return_response=return_response,
                    timeout=CUE_RESPONSE_TIMEOUT_SECONDS,
                )
        except EXPECTED_ERRORS as error:
            raise await self._lose_transport(f"service {name} failed: {error!r}") from error
        if response is not None and not response.success:
            self._logger.warning("cue service %s failed: %s", name, response.error_message)
        self._logger.debug("executed service %s with a response", name)
        return True

    async def _wait_cue_played(self, name: str, updates: int, called_at: float) -> None:
        """A cue without a response has played once the media player has reported a change
        after the call (ESPHome may merge `ANNOUNCING` and `IDLE` into one update), at least
        `CUE_MIN_SECONDS` after the call, and is not busy (ANNOUNCING or PLAYING). Bounded; a
        timeout is a WARNING, not a failure."""
        if self._media_player_key is None:
            return  # nothing to follow (no playback either)
        if not await self._wait_player(
            lambda: self._player_updates > updates, CUE_START_TIMEOUT_SECONDS
        ):
            self._logger.warning(
                "cue %s: the media player reported no change within %g s; going on",
                name,
                CUE_START_TIMEOUT_SECONDS,
            )
            return
        # The floor (review S1): the update may not be the cue's; by now its own report is in.
        # Waiting on the player, not sleeping, still notices a disconnect at once.
        floor = called_at + CUE_MIN_SECONDS - asyncio.get_running_loop().time()
        if floor > 0:
            await self._wait_player(lambda: False, floor)
        if not await self._wait_player(self._player_free, CUE_END_TIMEOUT_SECONDS):
            self._logger.warning(
                "cue %s: the media player is still busy after %g s; going on",
                name,
                CUE_END_TIMEOUT_SECONDS,
            )

    # Listening

    async def _start_listening(self, listen_id: str) -> None:
        assert self._listen_id is None, "StartListening while armed"
        assert self._playback is None, "StartListening during playback (MP-AUDIO-001)"
        await self._await_stale_stop()
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

    async def _await_stale_stop(self) -> None:
        """Let a pending stop of a stray device stream finish before the device is used."""
        if self._stale_stop is not None:
            await self._stale_stop  # bounded by its own timeouts
            self._stale_stop = None
            if self._unavailable is not None:
                raise MicrophoneUnavailable(self._unavailable)

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

    # Playback

    async def _begin_playback(self, event: PlaybackBegin) -> None:
        assert self._listen_id is None, "playback only while disarmed (MP-AUDIO-001)"
        assert self._playback is None, "PlaybackBegin during playback"
        server = self._playback_server
        if server is None:
            raise RuntimeError("this driver has no playback HTTP server")
        key, client = self._media_player_key, self._client
        if key is None:
            raise RuntimeError(f"ESPHome firmware at {self._address()} has no media player")
        assert client is not None  # _ensure_connected
        device_ip = self._device_ip
        if device_ip is None:  # reconnecting may pick an IPv4 address
            raise await self._lose_transport("the API connection is not IPv4: no playback URL")
        await self._await_stale_stop()
        # A Voice PE drops an announcement URL that arrives while it is still announcing a cue.
        if not await self._wait_player(self._player_free, PLAYER_FREE_TIMEOUT_SECONDS):
            self._logger.warning(
                "playback %s: the media player is still busy after %g s; sending anyway",
                event.playback_id,
                PLAYER_FREE_TIMEOUT_SECONDS,
            )
        try:
            host = self._local_ip(device_ip)
        except OSError as error:
            raise await self._lose_transport(f"no route to {device_ip}: {error!r}") from error
        stream = server.open_stream(  # ValueError for an unsupported format
            event.playback_id, event.rate, event.width, event.channels, device_ip
        )
        self._playback = _Playback(event.playback_id, stream)
        url = f"http://{host}:{server.port}{server.path(event.playback_id)}"
        try:
            if event.volume is not None:
                client.media_player_command(key, volume=event.volume)
                await asyncio.sleep(VOLUME_SETTLE_SECONDS)
            client.media_player_command(key, media_url=url, announcement=True)
        except EXPECTED_ERRORS as error:
            self._release_playback()
            raise await self._lose_transport(f"media player command failed: {error!r}") from error
        self._logger.info("playback playback_id=%s url=%s", event.playback_id, url)

    def _current_playback(self, playback_id: str) -> _Playback | None:
        if self._unavailable is not None:
            return None  # failed meanwhile; the reader gets MicrophoneUnavailable
        playback = self._playback
        assert playback is not None and playback.playback_id == playback_id, playback_id
        return playback

    def _write_playback(self, playback_id: str, data: bytes) -> None:
        if (playback := self._current_playback(playback_id)) is None:
            raise MicrophoneUnavailable(self._unavailable or "not connected")
        try:
            playback.stream.write(data)
        except StreamFull as error:
            self._release_playback()
            self._fail(str(error))
            raise MicrophoneUnavailable(str(error)) from error

    def _end_playback(self, playback_id: str) -> None:
        if (playback := self._current_playback(playback_id)) is None:
            raise MicrophoneUnavailable(self._unavailable or "not connected")
        assert playback.drain is None, "duplicate PlaybackEnd"
        playback.stream.end()
        playback.drain = asyncio.create_task(self._drain(playback), name=f"drain {playback_id}")

    async def _drain(self, playback: _Playback) -> None:
        """PlaybackFinished once the device has played it all (the old driver's estimate), or
        unavailable if the device did not fetch all of it. Any other error fails loudly."""
        try:
            await self._drain_stream(playback)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # a bug: re-raised from wait_for_event(), never lost
            self._logger.error(
                "playback %s drain failed: %s", playback.playback_id, traceback.format_exc()
            )
            self._release_playback()
            if self._unavailable is None:
                self._unavailable = f"playback drain failed: {error!r}"
                self._armed = False
                self._drain_events()
                self._events.put_nowait(_Raise(error))

    async def _drain_stream(self, playback: _Playback) -> None:
        stream = playback.stream
        try:
            async with asyncio.timeout(STREAM_REQUEST_TIMEOUT_SECONDS):
                await stream.requested.wait()
        except TimeoutError:
            self._fail(
                f"device did not fetch playback {playback.playback_id} within "
                f"{STREAM_REQUEST_TIMEOUT_SECONDS:g} s"
            )
            return
        # A stalled reader is cut off by the server's per-slice write timeout, which sets `done`;
        # this outer bound only covers a server that never finishes.
        done_timeout = STREAM_DONE_EXTRA_SECONDS + stream.audio_seconds
        try:
            async with asyncio.timeout(done_timeout):
                await stream.done.wait()
        except TimeoutError:
            pass  # incomplete: handled below
        if not stream.complete:  # review B1: like a stream that was never fetched
            self._fail(
                f"device got {stream.sent_bytes} of {stream.queued_bytes} bytes of playback "
                f"{playback.playback_id} (the GET failed or stalled)"
            )
            return
        if stream.first_audio_at is not None:
            await asyncio.sleep(max(0.0, stream.remaining_seconds() + PLAYBACK_DRAIN_GRACE_SECONDS))
        self._logger.info(
            "playback finished playback_id=%s audio_seconds=%.2f",
            playback.playback_id,
            stream.audio_seconds,
        )
        self._release_playback()
        self._emit(PlaybackFinished(playback.playback_id))

    def _release_playback(self) -> None:
        """Forget the playback: cancel its drain (unless that is the caller) and release the
        stream, so its GET ends and a later GET gets 404."""
        playback, self._playback = self._playback, None
        if playback is None:
            return
        if playback.drain is not None and playback.drain is not asyncio.current_task():
            playback.drain.cancel()
        if self._playback_server is not None:
            self._playback_server.release(playback.stream)

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
        kept: list[MicrophoneEvent | _Wake | _Raise] = []
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
