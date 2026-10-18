"""Protocol state machine tests.

Adapted from piotr_the_ai_server tests/test_microphone_protocol.py @ a299643; wake-word and
follow-up listening-mode cases removed (the hub only uses continuous listening).
"""

import pytest

from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    AudioProgress,
    Close,
    CueFinished,
    CueType,
    DriverClosed,
    ListeningStarted,
    ListeningStopped,
    MicrophoneCommand,
    MicrophoneEvent,
    MicrophoneUnavailable,
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
from piotr_voice_hub.satellites.protocol import (
    RECENT_ID_WINDOW,
    DriverState,
    MicrophoneProtocolState,
    ProtocolSnapshot,
)


def test_open_mic_generation_accepts_multiple_distinct_segments() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))
    protocol.event(ListeningStarted("listen-1"))

    for utterance_id in ("utterance-1", "utterance-2"):
        protocol.event(SpeechStarted("listen-1", utterance_id, 16000, 2, 1))
        protocol.event(SpeechEnded("listen-1", utterance_id, "completed"))

    assert protocol.snapshot.state is DriverState.LISTENING
    assert protocol.snapshot.listen_id == "listen-1"


def test_stop_requires_matching_listen_id() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))
    protocol.event(ListeningStarted("listen-1"))

    with pytest.raises(AssertionError, match="mismatched listen_id"):
        protocol.command(StopListening("stale-listen", "cancelled"))


def test_identifier_cannot_be_reused() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("same-id"))
    protocol.event(ListeningStarted("same-id"))
    protocol.command(StopListening("same-id", "cancelled"))
    protocol.event(ListeningStopped("same-id", "cancelled"))

    with pytest.raises(AssertionError, match="identifier reused"):
        protocol.command(PlayCue("same-id", CueType.UTTERANCE_ACCEPTED))


def test_playback_finishes_only_after_end() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))

    with pytest.raises(AssertionError, match="before PlaybackEnd"):
        protocol.event(PlaybackFinished("playback-1"))

    protocol.command(PlaybackEnd("playback-1"))
    protocol.event(PlaybackFinished("playback-1"))
    assert protocol.snapshot.state is DriverState.DISARMED


def test_cue_is_half_duplex() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))

    with pytest.raises(AssertionError, match="PlayCue invalid in arming"):
        protocol.command(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))


def test_cue_completion_requires_matching_id() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(PlayCue("cue-1", CueType.FOLLOW_UP_READY))

    with pytest.raises(AssertionError, match="mismatched cue_id"):
        protocol.event(CueFinished("stale-cue"))


def test_error_visual_cannot_be_commanded() -> None:
    with pytest.raises(ValueError, match="firmware-owned"):
        SetVisualState(VisualState.ERROR)


def test_visual_command_does_not_change_audio_state() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))
    protocol.command(SetVisualState(VisualState.IDLE))
    assert protocol.snapshot.state is DriverState.ARMING


def test_nested_speech_segment_is_rejected() -> None:
    protocol = _listening_protocol()
    protocol.event(SpeechStarted("listen-1", "utterance-1", 16000, 2, 1))
    with pytest.raises(AssertionError, match="SpeechStarted invalid in capturing"):
        protocol.event(SpeechStarted("listen-1", "utterance-2", 16000, 2, 1))


def test_audio_outside_segment_is_rejected() -> None:
    protocol = _listening_protocol()
    with pytest.raises(AssertionError, match="AudioChunk invalid in listening"):
        protocol.event(AudioChunk("listen-1", "utterance-1", b"audio"))


def test_mismatched_utterance_is_rejected() -> None:
    protocol = _listening_protocol()
    protocol.event(SpeechStarted("listen-1", "utterance-1", 16000, 2, 1))
    with pytest.raises(AssertionError, match="mismatched utterance_id"):
        protocol.event(AudioChunk("listen-1", "stale-utterance", b"audio"))


def test_playback_is_rejected_while_listening() -> None:
    protocol = _listening_protocol()
    with pytest.raises(AssertionError, match="PlaybackBegin invalid in listening"):
        protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))


def test_implicit_rearm_is_rejected() -> None:
    protocol = _listening_protocol()
    with pytest.raises(AssertionError, match="StartListening invalid in listening"):
        protocol.command(StartListening("listen-2"))


def test_unavailability_requires_active_operation_correlation() -> None:
    protocol = _listening_protocol()
    with pytest.raises(AssertionError, match="must identify"):
        protocol.event(MicrophoneUnavailable("offline"))


def test_close_is_terminal() -> None:
    protocol = _listening_protocol()
    protocol.command(Close())
    assert protocol.snapshot.state is DriverState.CLOSED
    with pytest.raises(AssertionError, match="in CLOSED"):
        protocol.command(SetVisualState(VisualState.IDLE))


def test_playback_rejects_chunk_after_end() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))
    protocol.command(PlaybackEnd("playback-1"))
    with pytest.raises(AssertionError, match="after PlaybackEnd"):
        protocol.command(PlaybackChunk("playback-1", b"late"))


@pytest.mark.parametrize("state", list(DriverState)[:-1])
def test_visual_commands_are_idempotent_in_every_connected_state(state: DriverState) -> None:
    """MP-VISUAL-002: visuals are independent of every non-closed audio state."""
    protocol = _protocol_in_state(state)
    before = protocol.snapshot

    protocol.command(SetVisualState(VisualState.PROCESSING))
    protocol.command(SetVisualState(VisualState.PROCESSING))

    assert protocol.snapshot == before


@pytest.mark.parametrize(
    ("event", "error"),
    [
        (ListeningStarted("stale-listen"), "mismatched listen_id"),
    ],
)
def test_arming_rejects_stale_listening_started(event: MicrophoneEvent, error: str) -> None:
    """MP-ID-003/MP-STATE-001: delayed start callbacks cannot arm a generation."""
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))

    with pytest.raises(AssertionError, match=error):
        protocol.event(event)


@pytest.mark.parametrize(
    "event",
    [
        AudioChunk("stale-listen", "utterance-1", b"audio"),
        AudioProgress("listen-1", "stale-utterance", 1, 5),
        SpeechEnded("listen-1", "stale-utterance", "completed"),
    ],
)
def test_capturing_rejects_every_stale_segment_event(event: MicrophoneEvent) -> None:
    """MP-ID-003: no stale segment event can complete or mutate active capture."""
    protocol = _listening_protocol()
    protocol.event(SpeechStarted("listen-1", "utterance-1", 16000, 2, 1))
    before = protocol.snapshot

    with pytest.raises(AssertionError, match="mismatched"):
        protocol.event(event)

    assert protocol.snapshot == before


@pytest.mark.parametrize(
    ("event", "error"),
    [
        (CueFinished("stale-cue"), "mismatched cue_id"),
        (PlaybackFinished("stale-playback"), "mismatched playback_id"),
    ],
)
def test_output_completion_rejects_stale_identifiers(event: MicrophoneEvent, error: str) -> None:
    """MP-ID-003: delayed output completion cannot drain a newer operation."""
    protocol = MicrophoneProtocolState()
    if isinstance(event, CueFinished):
        protocol.command(PlayCue("cue-1", CueType.FOLLOW_UP_READY))
    else:
        protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))
        protocol.command(PlaybackEnd("playback-1"))
    before = protocol.snapshot

    with pytest.raises(AssertionError, match=error):
        protocol.event(event)

    assert protocol.snapshot == before


def test_reset_candidate_rejects_unknown_utterance_without_mutating_generation() -> None:
    """MP-OPENMIC-002/MP-ID-003: reset is correlated to a completed active segment."""
    protocol = _listening_protocol()
    before = protocol.snapshot

    with pytest.raises(AssertionError, match="unknown utterance_id"):
        protocol.command(ResetWakeCandidate("listen-1", "stale-utterance"))

    assert protocol.snapshot == before


COMMAND_LEGAL_STATES = {
    "StartListening": {DriverState.DISARMED},
    "StopListening": {DriverState.ARMING, DriverState.LISTENING, DriverState.CAPTURING},
    "SetVisualState": set(DriverState) - {DriverState.CLOSED},
    "ResetWakeCandidate": {DriverState.LISTENING},
    "PlayCue": {DriverState.DISARMED},
    "PlaybackBegin": {DriverState.DISARMED},
    "PlaybackChunk": {DriverState.PLAYING_AUDIO},
    "PlaybackEnd": {DriverState.PLAYING_AUDIO},
    "Close": set(DriverState) - {DriverState.CLOSED},
}


EVENT_LEGAL_STATES = {
    "ListeningStarted": {DriverState.ARMING},
    "ListeningStopped": {DriverState.STOPPING},
    "SpeechStarted": {DriverState.LISTENING},
    "AudioChunk": {DriverState.CAPTURING},
    "AudioProgress": {DriverState.CAPTURING},
    "SpeechEnded": {DriverState.CAPTURING},
    "CueFinished": {DriverState.PLAYING_CUE},
    "PlaybackFinished": {DriverState.PLAYING_AUDIO},
    "MicrophoneUnavailable": {
        DriverState.ARMING,
        DriverState.LISTENING,
        DriverState.CAPTURING,
        DriverState.STOPPING,
        DriverState.PLAYING_CUE,
        DriverState.PLAYING_AUDIO,
    },
    "DriverClosed": set(DriverState) - {DriverState.CLOSED},
}


# Next state after a legal command or event; None means "unchanged".
COMMAND_NEXT_STATE: dict[str, DriverState | None] = {
    "StartListening": DriverState.ARMING,
    "StopListening": DriverState.STOPPING,
    "SetVisualState": None,
    "ResetWakeCandidate": None,
    "PlayCue": DriverState.PLAYING_CUE,
    "PlaybackBegin": DriverState.PLAYING_AUDIO,
    "PlaybackChunk": None,
    "PlaybackEnd": None,
    "Close": DriverState.CLOSED,
}
EVENT_NEXT_STATE: dict[str, DriverState | None] = {
    "ListeningStarted": DriverState.LISTENING,
    "ListeningStopped": DriverState.DISARMED,
    "SpeechStarted": DriverState.CAPTURING,
    "AudioChunk": None,
    "AudioProgress": None,
    "SpeechEnded": DriverState.LISTENING,
    "CueFinished": DriverState.DISARMED,
    "PlaybackFinished": DriverState.DISARMED,
    "MicrophoneUnavailable": DriverState.DISARMED,
    "DriverClosed": DriverState.CLOSED,
}


@pytest.mark.parametrize("state", list(DriverState))
@pytest.mark.parametrize("command_name", COMMAND_LEGAL_STATES)
def test_complete_driver_state_command_matrix(
    state: DriverState,
    command_name: str,
) -> None:
    """MP-STATE-001: every command/state pair matches the normative table."""
    protocol = _protocol_in_state(state, completed_utterance=command_name == "ResetWakeCandidate")
    command = _command_for_state(command_name)

    if command_name == "Close" and state is DriverState.CLOSED:
        before = protocol.snapshot
        protocol.command(command)
        assert protocol.snapshot == before
        return
    if state in COMMAND_LEGAL_STATES[command_name]:
        protocol.command(command)
        assert protocol.snapshot.state is (COMMAND_NEXT_STATE[command_name] or state)
    else:
        with pytest.raises(AssertionError):
            protocol.command(command)


@pytest.mark.parametrize("state", list(DriverState))
@pytest.mark.parametrize("event_name", EVENT_LEGAL_STATES)
def test_complete_driver_state_event_matrix(
    state: DriverState,
    event_name: str,
) -> None:
    """MP-STATE-001: every driver event/state pair matches the normative table."""
    protocol = _protocol_in_state(state)
    if event_name == "PlaybackFinished" and state is DriverState.PLAYING_AUDIO:
        protocol.command(PlaybackEnd("playback-1"))
    event = _event_for_state(event_name, state)

    if event_name == "DriverClosed" and state is DriverState.CLOSED:
        before = protocol.snapshot
        protocol.event(event)
        assert protocol.snapshot == before
        return
    if state in EVENT_LEGAL_STATES[event_name]:
        protocol.event(event)
        assert protocol.snapshot.state is (EVENT_NEXT_STATE[event_name] or state)
    else:
        with pytest.raises(AssertionError):
            protocol.event(event)


def test_reconnect_after_failure_during_capture() -> None:
    """MP-ERROR-001/MP-ID-003: after a failure, re-arm with a new id; old-generation events fail."""
    protocol = _listening_protocol()
    protocol.event(SpeechStarted("listen-1", "utterance-1", 16000, 2, 1))
    protocol.event(
        MicrophoneUnavailable("offline", listen_id="listen-1", utterance_id="utterance-1")
    )
    assert protocol.snapshot == ProtocolSnapshot(DriverState.DISARMED, None, None, None, None)

    with pytest.raises(AssertionError, match="identifier reused"):
        protocol.command(StartListening("listen-1"))
    protocol.command(StartListening("listen-2"))
    with pytest.raises(AssertionError, match="mismatched listen_id"):
        protocol.event(ListeningStarted("listen-1"))
    protocol.event(ListeningStarted("listen-2"))

    with pytest.raises(AssertionError, match="AudioChunk invalid in listening"):
        protocol.event(AudioChunk("listen-1", "utterance-1", b"late"))
    with pytest.raises(AssertionError, match="mismatched listen_id"):
        protocol.event(SpeechStarted("listen-1", "utterance-2", 16000, 2, 1))
    protocol.event(SpeechStarted("listen-2", "utterance-3", 16000, 2, 1))
    with pytest.raises(AssertionError, match="mismatched listen_id"):
        protocol.event(SpeechEnded("listen-1", "utterance-3", "completed"))
    assert protocol.snapshot.state is DriverState.CAPTURING


def test_reconnect_after_failure_during_playback() -> None:
    protocol = MicrophoneProtocolState()
    protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))
    protocol.event(MicrophoneUnavailable("offline", playback_id="playback-1"))
    assert protocol.snapshot.state is DriverState.DISARMED

    with pytest.raises(AssertionError, match="PlaybackFinished invalid in disarmed"):
        protocol.event(PlaybackFinished("playback-1"))
    protocol.command(PlaybackBegin("playback-2", 22050, 2, 1))
    protocol.command(PlaybackEnd("playback-2"))
    with pytest.raises(AssertionError, match="mismatched playback_id"):
        protocol.event(PlaybackFinished("playback-1"))
    protocol.event(PlaybackFinished("playback-2"))


def test_id_tracking_stays_bounded_under_continuous_listening() -> None:
    """Rule 1: weeks of VAD segments must not grow memory; recent reuse is still rejected."""
    protocol = _listening_protocol()
    cycles = RECENT_ID_WINDOW * 5
    for index in range(cycles):
        utterance_id = f"utterance-{index}"
        protocol.event(SpeechStarted("listen-1", utterance_id, 16000, 2, 1))
        protocol.event(SpeechEnded("listen-1", utterance_id, "completed"))
        assert len(protocol._used_ids) <= RECENT_ID_WINDOW
        assert len(protocol._recent_ids) <= RECENT_ID_WINDOW

    assert len(protocol._used_ids) == RECENT_ID_WINDOW
    assert protocol.snapshot.state is DriverState.LISTENING
    recent = f"utterance-{cycles - 1}"
    protocol.command(ResetWakeCandidate("listen-1", recent))
    with pytest.raises(AssertionError, match="identifier reused"):
        protocol.event(SpeechStarted("listen-1", recent, 16000, 2, 1))
    with pytest.raises(AssertionError, match="unknown utterance_id"):
        protocol.command(ResetWakeCandidate("listen-1", "utterance-0"))  # evicted long ago
    protocol.event(SpeechStarted("listen-1", f"utterance-{cycles}", 16000, 2, 1))
    assert protocol.snapshot.state is DriverState.CAPTURING


def _listening_protocol() -> MicrophoneProtocolState:
    protocol = MicrophoneProtocolState()
    protocol.command(StartListening("listen-1"))
    protocol.event(ListeningStarted("listen-1"))
    return protocol


def _protocol_in_state(
    state: DriverState,
    *,
    completed_utterance: bool = False,
) -> MicrophoneProtocolState:
    protocol = MicrophoneProtocolState()
    if state is DriverState.DISARMED:
        return protocol
    if state is DriverState.ARMING:
        protocol.command(StartListening("listen-1"))
        return protocol
    if state in (DriverState.LISTENING, DriverState.CAPTURING, DriverState.STOPPING):
        protocol.command(StartListening("listen-1"))
        protocol.event(ListeningStarted("listen-1"))
        if completed_utterance:
            protocol.event(SpeechStarted("listen-1", "utterance-completed", 16000, 2, 1))
            protocol.event(SpeechEnded("listen-1", "utterance-completed", "completed"))
        if state is DriverState.CAPTURING:
            protocol.event(SpeechStarted("listen-1", "utterance-1", 16000, 2, 1))
        elif state is DriverState.STOPPING:
            protocol.command(StopListening("listen-1", "test"))
        return protocol
    if state is DriverState.PLAYING_CUE:
        protocol.command(PlayCue("cue-1", CueType.FOLLOW_UP_READY))
        return protocol
    if state is DriverState.PLAYING_AUDIO:
        protocol.command(PlaybackBegin("playback-1", 22050, 2, 1))
        return protocol
    if state is DriverState.CLOSED:
        protocol.command(Close())
        return protocol
    raise AssertionError(f"unsupported test state: {state}")


def _command_for_state(command_name: str) -> MicrophoneCommand:
    return {
        "StartListening": StartListening("new-listen"),
        "StopListening": StopListening("listen-1", "test"),
        "SetVisualState": SetVisualState(VisualState.IDLE),
        "ResetWakeCandidate": ResetWakeCandidate("listen-1", "utterance-completed"),
        "PlayCue": PlayCue("new-cue", CueType.FOLLOW_UP_READY),
        "PlaybackBegin": PlaybackBegin("new-playback", 22050, 2, 1),
        "PlaybackChunk": PlaybackChunk("playback-1", b"audio"),
        "PlaybackEnd": PlaybackEnd("playback-1"),
        "Close": Close(),
    }[command_name]


def _event_for_state(event_name: str, state: DriverState) -> MicrophoneEvent:
    unavailable = {
        DriverState.ARMING: MicrophoneUnavailable("offline", listen_id="listen-1"),
        DriverState.LISTENING: MicrophoneUnavailable("offline", listen_id="listen-1"),
        DriverState.CAPTURING: MicrophoneUnavailable(
            "offline",
            listen_id="listen-1",
            utterance_id="utterance-1",
        ),
        DriverState.STOPPING: MicrophoneUnavailable("offline", listen_id="listen-1"),
        DriverState.PLAYING_CUE: MicrophoneUnavailable("offline", cue_id="cue-1"),
        DriverState.PLAYING_AUDIO: MicrophoneUnavailable(
            "offline",
            playback_id="playback-1",
        ),
    }.get(state, MicrophoneUnavailable("offline"))
    return {
        "ListeningStarted": ListeningStarted("listen-1"),
        "ListeningStopped": ListeningStopped("listen-1", "test"),
        "SpeechStarted": SpeechStarted("listen-1", "new-utterance", 16000, 2, 1),
        "AudioChunk": AudioChunk("listen-1", "utterance-1", b"audio"),
        "AudioProgress": AudioProgress("listen-1", "utterance-1", 1, 5),
        "SpeechEnded": SpeechEnded("listen-1", "utterance-1", "completed"),
        "CueFinished": CueFinished("cue-1"),
        "PlaybackFinished": PlaybackFinished("playback-1"),
        "MicrophoneUnavailable": unavailable,
        "DriverClosed": DriverClosed(),
    }[event_name]
