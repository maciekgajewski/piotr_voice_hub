"""Hub events (design.md §6), emitted by the satellite sessions. The API (T-011) adds the
envelope (`type`, `satellite`, `ts`) and serializes them."""

from dataclasses import dataclass
from enum import Enum


class SatelliteState(Enum):
    IDLE = "idle"  # armed, no wake candidate
    LISTENING = "listening"  # wake candidate open, or the follow-up window (T-020)
    PROCESSING = "processing"  # accepted; waiting for a reply
    SPEAKING = "speaking"  # playing a reply (T-017)
    ERROR = "error"  # offline


@dataclass(frozen=True)
class SatelliteStatus:
    satellite: str
    area: str
    connected: bool
    state: SatelliteState


@dataclass(frozen=True)
class Wake:
    satellite: str
    utterance_id: str


@dataclass(frozen=True)
class WakeCancelled:
    satellite: str
    utterance_id: str


@dataclass(frozen=True)
class TranscriptPartial:
    satellite: str
    utterance_id: str
    text: str  # after the wake phrase


@dataclass(frozen=True)
class Speaker:
    name: str
    confidence: float


@dataclass(frozen=True)
class TranscriptFinal:
    satellite: str
    utterance_id: str
    text: str  # after the wake phrase
    raw_text: str
    follow_up: bool
    speaker: Speaker | None  # a hint (T-018): None if not recognized or the service failed
    audio_ms: int


@dataclass(frozen=True)
class PlaybackFinished:
    """One per `ack`ed `speak`, also when the playback failed (T-017 plan)."""

    satellite: str
    playback_id: str
    listening_for_follow_up: bool  # the follow-up window opens next (T-020)


@dataclass(frozen=True)
class FollowUpTimeout:
    """The follow-up window passed with no request (T-020); the satellite re-arms idle."""

    satellite: str


type HubEvent = (
    SatelliteStatus
    | Wake
    | WakeCancelled
    | TranscriptPartial
    | TranscriptFinal
    | PlaybackFinished
    | FollowUpTimeout
)
