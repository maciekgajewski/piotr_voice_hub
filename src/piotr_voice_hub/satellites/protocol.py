"""Validator for the manager/driver operation state machine (docs/microphone-protocol.md).

Copied from piotr_the_ai_server ai_server/microphones/protocol.py @ a299643, trimmed to the
continuous (open-mic) listening mode: a completed speech segment always returns to LISTENING.
Violations fail an assert (MP-STATE-001, MP-ERROR-001).
"""

from collections import deque
from dataclasses import dataclass
from enum import Enum

from piotr_voice_hub.satellites.messages import (
    AudioChunk,
    AudioProgress,
    Close,
    CueFinished,
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
    PlaybackStop,
    PlaybackUrl,
    PlayCue,
    ResetWakeCandidate,
    SetVisualState,
    SpeechEnded,
    SpeechStarted,
    StartListening,
    StopListening,
)

# Reuse is checked against this many most recent identifiers. Under continuous listening every
# VAD segment claims one, so an unbounded set would grow for the whole connection lifetime.
# Process-wide uniqueness is the identifier factory's job (monotonic ids); this window catches
# delayed callbacks and reused recent ids (MP-ID-003).
RECENT_ID_WINDOW = 1024


class DriverState(Enum):
    DISARMED = "disarmed"
    ARMING = "arming"
    LISTENING = "listening"
    CAPTURING = "capturing"
    STOPPING = "stopping"
    PLAYING_CUE = "playing_cue"
    PLAYING_AUDIO = "playing_audio"
    CLOSED = "closed"


@dataclass(frozen=True)
class ProtocolSnapshot:
    state: DriverState
    listen_id: str | None
    utterance_id: str | None
    cue_id: str | None
    playback_id: str | None


class MicrophoneProtocolState:
    """Validates the normative manager/driver operation state machine."""

    def __init__(self) -> None:
        self._state = DriverState.DISARMED
        self._listen_id: str | None = None
        self._utterance_id: str | None = None
        self._cue_id: str | None = None
        self._playback_id: str | None = None
        self._playback_ended = False
        self._playback_stopped = False
        self._used_ids: set[str] = set()
        self._recent_ids: deque[str] = deque()

    @property
    def snapshot(self) -> ProtocolSnapshot:
        return ProtocolSnapshot(
            state=self._state,
            listen_id=self._listen_id,
            utterance_id=self._utterance_id,
            cue_id=self._cue_id,
            playback_id=self._playback_id,
        )

    def command(self, command: MicrophoneCommand) -> None:
        if isinstance(command, SetVisualState):
            self._require_not_closed(command)
            return
        if isinstance(command, ResetWakeCandidate):
            self._require_state(command, DriverState.LISTENING)
            self._require_equal("listen_id", command.listen_id, self._listen_id)
            assert command.utterance_id in self._used_ids, "unknown utterance_id"
            return
        if isinstance(command, Close):
            self._clear_operation()
            self._state = DriverState.CLOSED
            return
        if isinstance(command, StartListening):
            self._require_state(command, DriverState.DISARMED)
            self._claim_new_id(command.listen_id)
            self._listen_id = command.listen_id
            self._state = DriverState.ARMING
            return
        if isinstance(command, StopListening):
            self._require_state(
                command, DriverState.ARMING, DriverState.LISTENING, DriverState.CAPTURING
            )
            self._require_equal("listen_id", command.listen_id, self._listen_id)
            self._state = DriverState.STOPPING
            return
        if isinstance(command, PlayCue):
            self._require_state(command, DriverState.DISARMED)
            self._claim_new_id(command.cue_id)
            self._cue_id = command.cue_id
            self._state = DriverState.PLAYING_CUE
            return
        if isinstance(command, PlaybackBegin):
            self._require_state(command, DriverState.DISARMED)
            self._claim_new_id(command.playback_id)
            self._playback_id = command.playback_id
            self._playback_ended = False
            self._playback_stopped = False
            self._state = DriverState.PLAYING_AUDIO
            return
        if isinstance(command, PlaybackUrl):
            # T-021: a whole playback in one command; as if PlaybackEnd had been sent.
            self._require_state(command, DriverState.DISARMED)
            self._claim_new_id(command.playback_id)
            self._playback_id = command.playback_id
            self._playback_ended = True
            self._playback_stopped = False
            self._state = DriverState.PLAYING_AUDIO
            return
        if isinstance(command, PlaybackChunk):
            self._require_state(command, DriverState.PLAYING_AUDIO)
            self._require_equal("playback_id", command.playback_id, self._playback_id)
            assert not self._playback_ended, "PlaybackChunk after PlaybackEnd or PlaybackStop"
            return
        if isinstance(command, PlaybackEnd):
            self._require_state(command, DriverState.PLAYING_AUDIO)
            self._require_equal("playback_id", command.playback_id, self._playback_id)
            assert not self._playback_ended, "PlaybackEnd after PlaybackEnd or PlaybackStop"
            self._playback_ended = True
            return
        if isinstance(command, PlaybackStop):
            # Before or after PlaybackEnd; nothing more may be sent for this playback.
            self._require_state(command, DriverState.PLAYING_AUDIO)
            self._require_equal("playback_id", command.playback_id, self._playback_id)
            assert not self._playback_stopped, "duplicate PlaybackStop"
            self._playback_ended = self._playback_stopped = True
            return
        raise AssertionError(f"unsupported microphone command: {type(command).__name__}")

    def event(self, event: MicrophoneEvent) -> None:
        if isinstance(event, DriverClosed):
            self._clear_operation()
            self._state = DriverState.CLOSED
            return
        if isinstance(event, ListeningStarted):
            self._require_state(event, DriverState.ARMING)
            self._require_equal("listen_id", event.listen_id, self._listen_id)
            self._state = DriverState.LISTENING
            return
        if isinstance(event, ListeningStopped):
            self._require_state(event, DriverState.STOPPING)
            self._require_equal("listen_id", event.listen_id, self._listen_id)
            self._clear_listening()
            self._state = DriverState.DISARMED
            return
        if isinstance(event, SpeechStarted):
            self._require_state(event, DriverState.LISTENING)
            self._require_equal("listen_id", event.listen_id, self._listen_id)
            self._claim_new_id(event.utterance_id)
            self._utterance_id = event.utterance_id
            self._state = DriverState.CAPTURING
            return
        if isinstance(event, AudioChunk | AudioProgress):
            self._require_segment(event)
            return
        if isinstance(event, SpeechEnded):
            self._require_segment(event)
            self._utterance_id = None
            self._state = DriverState.LISTENING
            return
        if isinstance(event, CueFinished):
            self._require_state(event, DriverState.PLAYING_CUE)
            self._require_equal("cue_id", event.cue_id, self._cue_id)
            self._cue_id = None
            self._state = DriverState.DISARMED
            return
        if isinstance(event, PlaybackFinished):
            self._require_state(event, DriverState.PLAYING_AUDIO)
            self._require_equal("playback_id", event.playback_id, self._playback_id)
            assert self._playback_ended, "PlaybackFinished before PlaybackEnd"
            self._playback_id = None
            self._state = DriverState.DISARMED
            return
        if isinstance(event, MicrophoneUnavailable):
            self._validate_unavailable(event)
            self._clear_operation()
            self._state = DriverState.DISARMED
            return
        raise AssertionError(f"unsupported microphone event: {type(event).__name__}")

    def _require_segment(self, event: AudioChunk | AudioProgress | SpeechEnded) -> None:
        self._require_state(event, DriverState.CAPTURING)
        self._require_equal("listen_id", event.listen_id, self._listen_id)
        self._require_equal("utterance_id", event.utterance_id, self._utterance_id)

    def _validate_unavailable(self, event: MicrophoneUnavailable) -> None:
        correlations = {
            "listen_id": (event.listen_id, self._listen_id),
            "utterance_id": (event.utterance_id, self._utterance_id),
            "cue_id": (event.cue_id, self._cue_id),
            "playback_id": (event.playback_id, self._playback_id),
        }
        assert any(value is not None for value, _ in correlations.values()), (
            "MicrophoneUnavailable must identify the failed operation"
        )
        for name, (value, active) in correlations.items():
            if value is not None:
                self._require_equal(name, value, active)

    def _claim_new_id(self, identifier: str) -> None:
        assert identifier, "correlation identifier must be non-empty"
        assert identifier not in self._used_ids, f"correlation identifier reused: {identifier}"
        if len(self._recent_ids) == RECENT_ID_WINDOW:
            self._used_ids.remove(self._recent_ids.popleft())
        self._recent_ids.append(identifier)
        self._used_ids.add(identifier)

    def _require_not_closed(self, item: object) -> None:
        assert self._state is not DriverState.CLOSED, f"{type(item).__name__} in CLOSED"

    def _require_state(self, item: object, *states: DriverState) -> None:
        assert self._state in states, (
            f"{type(item).__name__} invalid in {self._state.value}; "
            f"expected {', '.join(state.value for state in states)}"
        )

    @staticmethod
    def _require_equal(name: str, value: object, expected: object) -> None:
        assert value == expected, f"mismatched {name}: received={value!r} active={expected!r}"

    def _clear_listening(self) -> None:
        self._listen_id = None
        self._utterance_id = None

    def _clear_operation(self) -> None:
        self._clear_listening()
        self._cue_id = None
        self._playback_id = None
        self._playback_ended = False
        self._playback_stopped = False
