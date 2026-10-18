"""Hub events (design.md §6), emitted by the satellite sessions. The API (T-011) adds the
envelope (`type`, `satellite`, `ts`) and serializes them."""

from dataclasses import dataclass
from enum import Enum


class SatelliteState(Enum):
    IDLE = "idle"  # armed, no wake candidate
    LISTENING = "listening"  # wake candidate open
    PROCESSING = "processing"  # accepted; waiting for a reply
    ERROR = "error"  # offline
    # SPEAKING arrives with playback (P1).


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
    speaker: Speaker | None  # speaker recognition is P1: always None in P0
    audio_ms: int


type HubEvent = SatelliteStatus | Wake | WakeCancelled | TranscriptPartial | TranscriptFinal
