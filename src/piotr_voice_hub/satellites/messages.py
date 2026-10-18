"""Manager <-> satellite driver protocol messages (docs/microphone-protocol.md).

Copied from piotr_the_ai_server ai_server/microphones/messages.py @ a299643, trimmed to the
continuous (open-mic) listening mode, the only mode the hub uses.
"""

from dataclasses import dataclass
from enum import Enum


class VisualState(Enum):
    ERROR = "error"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"


class CueType(Enum):
    UTTERANCE_ACCEPTED = "utterance_accepted"
    FOLLOW_UP_READY = "follow_up_ready"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class StartListening:
    listen_id: str


@dataclass(frozen=True)
class StopListening:
    listen_id: str
    reason: str


@dataclass(frozen=True)
class SetVisualState:
    state: VisualState

    def __post_init__(self) -> None:
        if self.state is VisualState.ERROR:
            raise ValueError("ERROR is firmware-owned and cannot be commanded")


@dataclass(frozen=True)
class ResetWakeCandidate:
    listen_id: str
    utterance_id: str


@dataclass(frozen=True)
class PlayCue:
    cue_id: str
    cue_type: CueType


@dataclass(frozen=True)
class PlaybackBegin:
    playback_id: str
    rate: int
    width: int
    channels: int
    volume: float | None = None


@dataclass(frozen=True)
class PlaybackChunk:
    playback_id: str
    data: bytes


@dataclass(frozen=True)
class PlaybackEnd:
    playback_id: str


@dataclass(frozen=True)
class Close:
    pass


@dataclass(frozen=True)
class ListeningStarted:
    listen_id: str


@dataclass(frozen=True)
class ListeningStopped:
    listen_id: str
    reason: str


@dataclass(frozen=True)
class SpeechStarted:
    listen_id: str
    utterance_id: str
    rate: int
    width: int
    channels: int


@dataclass(frozen=True)
class AudioChunk:
    listen_id: str
    utterance_id: str
    data: bytes


@dataclass(frozen=True)
class AudioProgress:
    listen_id: str
    utterance_id: str
    chunks: int
    bytes: int


@dataclass(frozen=True)
class SpeechEnded:
    listen_id: str
    utterance_id: str
    reason: str


@dataclass(frozen=True)
class CueFinished:
    cue_id: str


@dataclass(frozen=True)
class PlaybackFinished:
    playback_id: str


@dataclass(frozen=True)
class MicrophoneUnavailable:
    reason: str
    listen_id: str | None = None
    utterance_id: str | None = None
    cue_id: str | None = None
    playback_id: str | None = None


@dataclass(frozen=True)
class DriverClosed:
    pass


type MicrophoneCommand = (
    StartListening
    | StopListening
    | SetVisualState
    | ResetWakeCandidate
    | PlayCue
    | PlaybackBegin
    | PlaybackChunk
    | PlaybackEnd
    | Close
)
type MicrophoneEvent = (
    ListeningStarted
    | ListeningStopped
    | SpeechStarted
    | AudioChunk
    | AudioProgress
    | SpeechEnded
    | CueFinished
    | PlaybackFinished
    | MicrophoneUnavailable
    | DriverClosed
)
