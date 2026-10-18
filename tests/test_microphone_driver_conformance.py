"""Runs the reusable conformance checks against a minimal in-memory driver.

This proves the checks themselves are consistent with MicrophoneProtocolState; the ESPHome
driver (T-007) will run the same checks.
"""

import asyncio

from microphone_driver_conformance import (
    assert_cue_contract,
    assert_listening_and_capture_contract,
    assert_playback_contract,
    assert_stop_drops_capture_events_contract,
)

from piotr_voice_hub.satellites.interfaces import Microphone
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    CueFinished,
    ListeningStarted,
    ListeningStopped,
    MicrophoneCommand,
    MicrophoneEvent,
    PlaybackEnd,
    PlaybackFinished,
    PlayCue,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
)
from piotr_voice_hub.satellites.protocol import DriverState, MicrophoneProtocolState
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget

TIMEOUT_SECONDS = 1.0
CAPTURE_EVENTS = (SpeechStarted, AudioChunk, SpeechEnded)


class FakeMicrophone:
    """Answers commands immediately and validates both directions with the protocol state."""

    def __init__(self) -> None:
        self.context = MicrophoneContext("fake", "fake-1", "test")
        self.playback_target = PlaybackTarget("fake", "fake-1", "fake.local", "key")
        self._protocol = MicrophoneProtocolState()
        self._events: asyncio.Queue[MicrophoneEvent] = asyncio.Queue(maxsize=16)
        self._utterances = 0

    async def wait_for_event(self) -> MicrophoneEvent:
        return await asyncio.wait_for(self._events.get(), TIMEOUT_SECONDS)

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        self._protocol.command(event)
        if isinstance(event, StartListening):
            self._emit(ListeningStarted(event.listen_id))
        elif isinstance(event, StopListening):
            # MP-STOP-002: drop queued capture events of the stopped generation.
            kept = [e for e in self._drain() if not isinstance(e, CAPTURE_EVENTS)]
            for kept_event in kept:
                self._events.put_nowait(kept_event)
            self._emit(ListeningStopped(event.listen_id, event.reason))
        elif isinstance(event, PlayCue):
            self._emit(CueFinished(event.cue_id))
        elif isinstance(event, PlaybackEnd):
            self._emit(PlaybackFinished(event.playback_id))

    async def close(self) -> None:
        return None

    async def emit_speech(self, data: bytes) -> None:
        snapshot = self._protocol.snapshot
        if snapshot.state is not DriverState.LISTENING or snapshot.listen_id is None:
            return  # not armed: late audio is dropped
        listen_id = snapshot.listen_id
        self._utterances += 1
        utterance_id = f"utterance-{self._utterances}"
        self._emit(SpeechStarted(listen_id, utterance_id, 16000, 2, 1))
        self._emit(AudioChunk(listen_id, utterance_id, data))
        self._emit(SpeechEnded(listen_id, utterance_id, "completed"))

    def _drain(self) -> list[MicrophoneEvent]:
        drained = []
        while not self._events.empty():
            drained.append(self._events.get_nowait())
        return drained

    def _emit(self, event: MicrophoneEvent) -> None:
        self._protocol.event(event)
        self._events.put_nowait(event)


class FakeStimulus:
    speech = b"audio"

    def __init__(self, microphone: FakeMicrophone) -> None:
        self.microphone: Microphone = microphone
        self._fake = microphone

    async def emit_speech(self, data: bytes) -> None:
        await self._fake.emit_speech(data)


def test_fake_driver_passes_listening_and_capture_contract() -> None:
    asyncio.run(assert_listening_and_capture_contract(FakeStimulus(FakeMicrophone())))


def test_fake_driver_passes_stop_contract() -> None:
    asyncio.run(assert_stop_drops_capture_events_contract(FakeStimulus(FakeMicrophone())))


def test_fake_driver_passes_cue_contract() -> None:
    asyncio.run(assert_cue_contract(FakeMicrophone()))


def test_fake_driver_passes_playback_contract() -> None:
    asyncio.run(assert_playback_contract(FakeMicrophone()))
