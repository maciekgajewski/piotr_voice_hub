"""Reusable black-box driver conformance checks (docs/microphone-protocol.md).

Adapted from piotr_the_ai_server tests/microphone_driver_conformance.py @ a299643: the listening
contract no longer takes a mode (continuous listening only) and always stops the generation;
it accepts any AudioChunk split (VAD drivers add pre-roll), and MP-STOP-002 is checked (T-007).
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
    speech: bytes  # audio the driver's speech detector treats as speech

    async def emit_speech(self, data: bytes) -> None:
        """Feed `data` as one speech segment (with whatever surrounds it for the detector)."""
        raise NotImplementedError


async def assert_listening_and_capture_contract(stimulus: DriverStimulus) -> None:
    """Reusable black-box checks for MP-ID-001/002 and MP-STATE-001."""
    microphone = stimulus.microphone
    listen_id = "listen-open-mic"
    await microphone.send_output_event(StartListening(listen_id))
    assert await microphone.wait_for_event() == ListeningStarted(listen_id)

    await stimulus.emit_speech(stimulus.speech)
    started = await microphone.wait_for_event()
    assert isinstance(started, SpeechStarted)
    assert started.listen_id == listen_id
    assert started.utterance_id
    assert (started.rate, started.width, started.channels) == (16000, 2, 1)
    captured = bytearray()
    while isinstance(event := await microphone.wait_for_event(), AudioChunk):
        assert (event.listen_id, event.utterance_id) == (listen_id, started.utterance_id)
        captured += event.data
    assert event == SpeechEnded(listen_id, started.utterance_id, "completed")
    # A VAD driver may add pre-roll and trailing silence, but never loses the speech itself.
    assert stimulus.speech in captured

    await microphone.send_output_event(StopListening(listen_id, "test_complete"))
    assert await microphone.wait_for_event() == ListeningStopped(listen_id, "test_complete")


async def assert_stop_drops_capture_events_contract(stimulus: DriverStimulus) -> None:
    """MP-STOP-002: capture events queued before StopListening, or arriving after it, are
    dropped; ListeningStopped is the next event and nothing of the old generation follows."""
    microphone = stimulus.microphone
    listen_id = "listen-stop-002"
    await microphone.send_output_event(StartListening(listen_id))
    assert await microphone.wait_for_event() == ListeningStarted(listen_id)
    await stimulus.emit_speech(stimulus.speech)  # queued, never read
    await microphone.send_output_event(StopListening(listen_id, "test_stop"))
    assert await microphone.wait_for_event() == ListeningStopped(listen_id, "test_stop")
    await stimulus.emit_speech(stimulus.speech)  # late audio of the stopped generation
    await microphone.send_output_event(PlayCue("cue-after-stop", CueType.TIMEOUT))
    assert await microphone.wait_for_event() == CueFinished("cue-after-stop")


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
