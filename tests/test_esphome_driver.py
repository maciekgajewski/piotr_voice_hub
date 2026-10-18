"""ESPHome driver tests against a fake aioesphomeapi client (no device, no network)."""

import asyncio
import logging
import wave
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from microphone_driver_conformance import (
    assert_cue_contract,
    assert_listening_and_capture_contract,
    assert_stop_drops_capture_events_contract,
)

from piotr_voice_hub.config import SatelliteConfig
from piotr_voice_hub.satellites import esphome
from piotr_voice_hub.satellites.esphome import EsphomeSatellite
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
from piotr_voice_hub.satellites.protocol import MicrophoneProtocolState
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget
from piotr_voice_hub.satellites.vad import (
    FRAME_BYTES,
    Hysteresis,
    SileroVad,
    VadTransition,
    load_session,
)

TIMEOUT = 2.0
SPEECH_CLIP = Path(__file__).parent / "data" / "speech_pl_16k.wav"
PRE_ROLL_FRAMES = 31  # int(1.0 s / 32 ms)
ALL_SERVICES = (
    "start_open_mic_listening",
    "stop_listening",
    "reset_open_mic_wake_candidate",
    "set_visual_idle",
    "set_visual_listening",
    "set_visual_processing",
    "play_message_end_cue",
    "play_conversation_timeout_cue",
    "play_follow_up_ready_cue",
)


def config(end_silence: float = 0.32, max_segment: float = 15.0) -> SatelliteConfig:
    return SatelliteConfig(
        "kitchen", "kitchen", "10.0.0.5", "a2V5", end_silence, max_segment, 30, 8
    )


class LoudnessVad:
    """Fake Silero: a frame with any sample above 1000 is speech (p 0.9); real hysteresis."""

    def __init__(self, end_silence_seconds: float) -> None:
        self._hysteresis = Hysteresis(end_silence_seconds)

    @property
    def speaking(self) -> bool:
        return self._hysteresis.speaking

    def process_frame(self, pcm: bytes) -> tuple[float, VadTransition | None]:
        assert len(pcm) == FRAME_BYTES
        loud = np.abs(np.frombuffer(pcm, "<i2").astype(np.int32)).max() > 1000
        probability = 0.9 if loud else 0.0
        return probability, self._hysteresis.update(probability)

    def reset(self) -> None:
        self._hysteresis.reset()

    def force_end(self) -> None:
        self._hysteresis.force_end()


class Service:
    def __init__(self, name: str) -> None:
        self.name = name
        self.key = hash(name)


type Handler = Callable[..., Coroutine[Any, Any, Any]]


class FakeClient:
    """Fake aioesphomeapi.APIClient: services, voice-assistant callbacks, transport stop."""

    def __init__(self, services: tuple[str, ...] = ALL_SERVICES) -> None:
        self.services = services
        self.calls: list[str] = []
        self.va_events: list[Any] = []
        self.connected = False
        self.disconnects = 0
        self.ack_stop = True
        self.hang: set[str] = set()  # connect / service names that never return
        self.started = asyncio.Event()
        self.on_stop: Callable[[bool], Coroutine[Any, Any, None]] | None = None
        self.handlers: dict[str, Handler] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    async def connect(self, on_stop: Any = None, login: bool = False) -> None:
        if "connect" in self.hang:
            await asyncio.Event().wait()
        self.on_stop = on_stop
        self.connected = True

    async def disconnect(self, force: bool = False) -> None:
        self.connected = False
        self.disconnects += 1

    async def list_entities_services(self) -> tuple[list[Any], list[Any]]:
        return [], [Service(name) for name in self.services]

    async def execute_service(self, service: Any, data: dict[str, Any]) -> None:
        if service.name in self.hang:
            await asyncio.Event().wait()
        self.calls.append(service.name)
        if service.name == "start_open_mic_listening":
            self._later(self._start())  # the device answers after the service returns
        elif service.name == "stop_listening" and self.ack_stop:
            self._later(self.handlers["stop"](False))

    def subscribe_voice_assistant(self, **handlers: Handler) -> Callable[[], None]:
        self.handlers = {name.removeprefix("handle_"): h for name, h in handlers.items()}
        return self.handlers.clear

    def send_voice_assistant_event(self, event_type: Any, data: Any) -> None:
        self.va_events.append(event_type)

    async def _start(self) -> None:
        await self.handlers["start"]("conversation", 0, None, None)
        self.started.set()

    def _later(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def audio(self, pcm: bytes, chunk_bytes: int = 640) -> None:
        """Feed audio in device-sized chunks that don't line up with VAD frames."""
        await asyncio.wait_for(self.started.wait(), TIMEOUT)
        for offset in range(0, len(pcm), chunk_bytes):
            if "audio" in self.handlers:
                await self.handlers["audio"](pcm[offset : offset + chunk_bytes], None)


class Clients:
    """Client factory that records every connection the driver makes."""

    def __init__(self) -> None:
        self.made: list[FakeClient] = []
        self.services: tuple[str, ...] = ALL_SERVICES

    def __call__(self, target: PlaybackTarget) -> FakeClient:
        assert target.address == "10.0.0.5" and target.api_key == "a2V5"
        client = FakeClient(self.services)
        self.made.append(client)
        return client

    @property
    def last(self) -> FakeClient:
        return self.made[-1]


class Checked:
    """Wraps the driver and validates every command and event with the protocol state."""

    def __init__(self, driver: EsphomeSatellite) -> None:
        self.driver = driver
        self.context: MicrophoneContext = driver.context
        self.playback_target: PlaybackTarget = driver.playback_target
        self.protocol = MicrophoneProtocolState()

    async def wait_for_event(self) -> MicrophoneEvent:
        event = await asyncio.wait_for(self.driver.wait_for_event(), TIMEOUT)
        self.protocol.event(event)
        return event

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self.protocol.command(event)
        await asyncio.wait_for(self.driver.send_output_event(event), TIMEOUT)

    async def close(self) -> None:
        await self.driver.close()


def silence(seconds: float) -> bytes:
    return b"\0\0" * int(seconds * 16000)


def loud(seconds: float) -> bytes:
    return np.full(int(seconds * 16000), 8000, dtype="<i2").tobytes()


def make(
    vad: esphome.Vad | None = None, cfg: SatelliteConfig | None = None
) -> tuple[Checked, Clients]:
    clients = Clients()
    cfg = cfg or config()
    driver = EsphomeSatellite(cfg, vad or LoudnessVad(cfg.end_silence_seconds), clients)
    return Checked(driver), clients


class Stimulus:
    def __init__(self, microphone: Checked, clients: Clients, speech: bytes) -> None:
        self.microphone: Microphone = microphone
        self.speech = speech
        self._clients = clients

    async def emit_speech(self, data: bytes) -> None:
        client = self._clients.last
        if not client.handlers:
            return
        await client.audio(silence(0.5) + data + silence(1.0))


async def arm(mic: Checked, listen_id: str = "listen-1") -> None:
    await mic.send_output_event(StartListening(listen_id))
    assert await mic.wait_for_event() == ListeningStarted(listen_id)


async def read_segment(mic: Checked) -> tuple[SpeechStarted, bytes, SpeechEnded]:
    started = await mic.wait_for_event()
    assert isinstance(started, SpeechStarted)
    captured = bytearray()
    while isinstance(event := await mic.wait_for_event(), AudioChunk):
        captured += event.data
    assert isinstance(event, SpeechEnded)
    return started, bytes(captured), event


# Conformance


def test_passes_listening_and_capture_contract() -> None:
    async def run() -> None:
        mic, clients = make()
        await assert_listening_and_capture_contract(Stimulus(mic, clients, loud(0.6)))
        assert clients.last.calls == ["start_open_mic_listening", "stop_listening"]

    asyncio.run(run())


def test_passes_stop_contract_mp_stop_002() -> None:
    async def run() -> None:
        mic, clients = make()
        await assert_stop_drops_capture_events_contract(Stimulus(mic, clients, loud(0.6)))

    asyncio.run(run())


def test_passes_cue_contract() -> None:
    async def run() -> None:
        mic, clients = make()
        await assert_cue_contract(mic)
        assert clients.last.calls == ["play_message_end_cue"]

    asyncio.run(run())


def test_passes_capture_contract_with_real_silero_on_real_speech() -> None:
    with wave.open(str(SPEECH_CLIP), "rb") as clip:
        speech = clip.readframes(clip.getnframes())

    async def run() -> None:
        mic, clients = make(SileroVad(load_session(), 0.8), config(end_silence=0.8))
        await assert_listening_and_capture_contract(Stimulus(mic, clients, speech))

    asyncio.run(run())


# Segmentation


def test_pre_roll_sends_the_second_before_the_start_including_the_onset() -> None:
    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        lead = silence(2.0)
        await clients.last.audio(lead + loud(0.5) + silence(1.0))
        _, captured, ended = await read_segment(mic)
        assert ended.reason == "completed"
        # SpeechStarted fires on the 8th loud frame; the pre-roll is the 31 frames up to it.
        onset = len(lead) // FRAME_BYTES
        start_frame = onset + 7
        assert (
            captured[: PRE_ROLL_FRAMES * FRAME_BYTES]
            == (lead + loud(0.5))[
                (start_frame - PRE_ROLL_FRAMES + 1) * FRAME_BYTES : (start_frame + 1) * FRAME_BYTES
            ]
        )
        assert loud(0.5) in captured

    asyncio.run(run())


def test_pre_roll_is_only_what_arrived_since_arming() -> None:
    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await clients.last.audio(silence(0.064) + loud(0.5) + silence(1.0))
        _, captured, _ = await read_segment(mic)
        assert captured.startswith(silence(0.064) + loud(0.2))

    asyncio.run(run())


def test_max_segment_cut_ends_and_the_next_segment_needs_a_new_start() -> None:
    async def run() -> None:
        mic, clients = make(cfg=config(max_segment=0.64))  # 20 frames
        await arm(mic)
        speech = loud(2.0)
        await clients.last.audio(speech + silence(1.0))
        first, a, first_end = await read_segment(mic)
        second, b, _ = await read_segment(mic)
        assert first_end.reason == "max_length"
        assert len(a) == 20 * FRAME_BYTES
        assert first.utterance_id != second.utterance_id
        assert len(a) + len(b) <= len(speech) + len(silence(1.0))  # nothing sent twice

    asyncio.run(run())


def test_segments_carry_process_unique_ids_across_a_reconnect() -> None:
    async def run() -> None:
        mic, clients = make()
        seen: list[str] = []
        for attempt in range(2):
            await arm(mic, f"listen-{attempt}")
            for _ in range(2):
                await clients.last.audio(loud(0.5) + silence(1.0))
                started, _, _ = await read_segment(mic)
                seen.append(started.utterance_id)
            assert clients.last.on_stop is not None
            await clients.last.on_stop(False)  # the transport drops
            with pytest.raises(MicrophoneUnavailable, match="transport stopped"):
                await mic.wait_for_event()
            await mic.close()
            mic.protocol = MicrophoneProtocolState()  # the session recreates its boundary
        assert len(clients.made) == 2  # reconnected
        assert len(set(seen)) == 4
        assert all(i.startswith("utterance-") for i in seen)

    asyncio.run(run())


def test_loud_non_speech_with_real_silero_opens_no_segment() -> None:
    with wave.open(str(Path(__file__).parent / "data" / "alarm_tone_16k.wav"), "rb") as clip:
        alarm = clip.readframes(clip.getnframes())

    async def run() -> None:
        mic, clients = make(SileroVad(load_session(), 0.8), config(end_silence=0.8))
        await arm(mic)
        await clients.last.audio(alarm + alarm)
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")

    asyncio.run(run())


# Stop, late events, failures


def test_stop_waits_for_the_device_ack_then_sends_run_end() -> None:
    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await asyncio.wait_for(clients.last.started.wait(), TIMEOUT)
        await mic.send_output_event(StopListening("listen-1", "accepted"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "accepted")
        assert clients.last.calls[-1] == "stop_listening"
        assert len(clients.last.va_events) == 1  # VOICE_ASSISTANT_RUN_END after the ack

    asyncio.run(run())


def test_missing_stop_ack_discards_the_generation_and_recovers_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(esphome, "STOP_ACK_TIMEOUT_SECONDS", 0.05)

    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await asyncio.wait_for(clients.last.started.wait(), TIMEOUT)
        clients.last.ack_stop = False
        with pytest.raises(MicrophoneUnavailable, match="no stop acknowledgement"):
            await mic.driver.send_output_event(StopListening("listen-1", "x"))
        assert clients.last.disconnects == 1
        await mic.close()
        mic.protocol = MicrophoneProtocolState()
        await arm(mic, "listen-2")  # a fresh connection
        assert len(clients.made) == 2

    asyncio.run(run())


def test_late_start_and_audio_after_stop_are_dropped() -> None:
    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await mic.send_output_event(StopListening("listen-1", "x"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "x")
        client = clients.last
        assert await client.handlers["start"]("late", 0, None, None) == 0
        await client.handlers["audio"](loud(1.0), None)
        await client.handlers["stop"](False)  # stale stop while disarmed is harmless
        await mic.send_output_event(PlayCue("cue-1", CueType.TIMEOUT))
        assert await mic.wait_for_event() == CueFinished("cue-1")

    asyncio.run(run())


def test_device_ending_its_stream_while_listening_fails_loudly() -> None:
    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await asyncio.wait_for(clients.last.started.wait(), TIMEOUT)
        await clients.last.handlers["stop"](True)
        with pytest.raises(MicrophoneUnavailable, match="ended its voice-assistant stream"):
            await mic.wait_for_event()
        with pytest.raises(MicrophoneUnavailable):  # stays unavailable until close()
            await mic.driver.send_output_event(SetVisualState(VisualState.IDLE))

    asyncio.run(run())


def test_connect_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(esphome, "CONNECT_TIMEOUT_SECONDS", 0.05)

    async def run() -> None:
        clients = Clients()
        original = clients.__call__

        def hanging(target: PlaybackTarget) -> FakeClient:
            client = original(target)
            client.hang.add("connect")
            return client

        driver = EsphomeSatellite(config(), LoudnessVad(0.32), hanging)
        with pytest.raises(MicrophoneUnavailable, match="connect to 10.0.0.5:6053 failed"):
            await driver.send_output_event(SetVisualState(VisualState.IDLE))
        assert clients.last.disconnects == 1

    asyncio.run(run())


def test_service_timeout_is_unavailable_and_drops_the_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(esphome, "SERVICE_TIMEOUT_SECONDS", 0.05)

    async def run() -> None:
        mic, clients = make()
        await mic.send_output_event(SetVisualState(VisualState.IDLE))
        clients.last.hang.add("set_visual_processing")
        with pytest.raises(MicrophoneUnavailable, match="set_visual_processing failed"):
            await mic.driver.send_output_event(SetVisualState(VisualState.PROCESSING))
        assert clients.last.disconnects == 1

    asyncio.run(run())


def test_stuck_reader_overflows_into_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(esphome, "MAX_QUEUED_EVENTS", 8)

    async def run() -> None:
        mic, clients = make()
        await arm(mic)
        await clients.last.audio(loud(1.0))  # more than 8 events, nobody reads
        with pytest.raises(MicrophoneUnavailable, match="event queue full"):
            await mic.wait_for_event()

    asyncio.run(run())


# Services


def test_visual_states_cues_and_wake_candidate_reset_call_the_firmware_services() -> None:
    async def run() -> None:
        mic, clients = make()
        for state in (VisualState.IDLE, VisualState.LISTENING, VisualState.PROCESSING):
            await mic.send_output_event(SetVisualState(state))
        for index, cue in enumerate(CueType):
            await mic.send_output_event(PlayCue(f"cue-{index}", cue))
            assert await mic.wait_for_event() == CueFinished(f"cue-{index}")
        await arm(mic)
        await clients.last.audio(loud(0.5) + silence(1.0))
        started, _, _ = await read_segment(mic)
        await mic.send_output_event(ResetWakeCandidate("listen-1", started.utterance_id))
        assert clients.last.calls == [
            "set_visual_idle",
            "set_visual_listening",
            "set_visual_processing",
            "play_message_end_cue",
            "play_follow_up_ready_cue",
            "play_conversation_timeout_cue",
            "start_open_mic_listening",
            "reset_open_mic_wake_candidate",
        ]

    asyncio.run(run())


def test_missing_required_service_fails_loudly_missing_reset_only_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        mic, clients = make()
        clients.services = ("start_open_mic_listening", "stop_listening")
        with pytest.raises(RuntimeError, match="no set_visual_idle service"):
            await mic.driver.send_output_event(SetVisualState(VisualState.IDLE))
        await arm(mic)
        await clients.last.audio(loud(0.5) + silence(1.0))
        started, _, _ = await read_segment(mic)
        with caplog.at_level(logging.WARNING):
            await mic.send_output_event(ResetWakeCandidate("listen-1", started.utterance_id))
        assert "firmware has no reset_open_mic_wake_candidate service" in caplog.text

    asyncio.run(run())


@pytest.mark.parametrize(
    "command",
    [PlaybackBegin("p-1", 22050, 2, 1), PlaybackChunk("p-1", b"x"), PlaybackEnd("p-1")],
)
def test_playback_fails_loudly_until_t014(command: MicrophoneCommand) -> None:
    async def run() -> None:
        mic, _ = make()
        with pytest.raises(NotImplementedError, match="T-014"):
            await mic.driver.send_output_event(command)

    asyncio.run(run())


def test_context_and_playback_target_come_from_config() -> None:
    mic, _ = make()
    assert mic.context == MicrophoneContext("esphome", "kitchen", "kitchen")
    assert mic.playback_target == PlaybackTarget("esphome", "kitchen", "10.0.0.5", "a2V5")
