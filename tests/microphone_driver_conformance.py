"""Reusable black-box driver conformance checks (docs/microphone-protocol.md).

Adapted from piotr_the_ai_server tests/microphone_driver_conformance.py @ a299643: the listening
contract no longer takes a mode (continuous listening only) and always stops the generation.
"""

from typing import Protocol

from piotr_voice_hub.satellites.interfaces import Microphone
from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    CueFinished,
    CueType,
    ListeningStarted,
    ListeningStopped,
    PlaybackBegin,
    PlaybackChunk,
    PlaybackEnd,
    PlaybackFinished,
    PlayCue,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
)


class DriverStimulus(Protocol):
    microphone: Microphone

    async def emit_speech(self, data: bytes, reason: str = "completed") -> None:
        raise NotImplementedError


async def assert_listening_and_capture_contract(stimulus: DriverStimulus) -> None:
    """Reusable black-box checks for MP-ID-001/002 and MP-STATE-001."""
    microphone = stimulus.microphone
    listen_id = "listen-open-mic"
    await microphone.send_output_event(StartListening(listen_id))
    assert await microphone.wait_for_event() == ListeningStarted(listen_id)

    await stimulus.emit_speech(b"audio")
    started = await microphone.wait_for_event()
    assert isinstance(started, SpeechStarted)
    assert started.listen_id == listen_id
    assert started.utterance_id
    assert await microphone.wait_for_event() == AudioChunk(
        listen_id, started.utterance_id, b"audio"
    )
    assert await microphone.wait_for_event() == SpeechEnded(
        listen_id, started.utterance_id, "completed"
    )

    await microphone.send_output_event(StopListening(listen_id, "test_complete"))
    assert await microphone.wait_for_event() == ListeningStopped(listen_id, "test_complete")


async def assert_cue_contract(microphone: Microphone) -> None:
    """Reusable black-box check for correlated cue completion."""
    await microphone.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
    assert await microphone.wait_for_event() == CueFinished("cue-1")


async def assert_playback_contract(microphone: Microphone) -> None:
    """Reusable black-box check for MP-AUDIO-001 drain completion."""
    await microphone.send_output_event(PlaybackBegin("playback-1", 22050, 2, 1))
    await microphone.send_output_event(PlaybackChunk("playback-1", b"audio"))
    await microphone.send_output_event(PlaybackEnd("playback-1"))
    assert await microphone.wait_for_event() == PlaybackFinished("playback-1")
