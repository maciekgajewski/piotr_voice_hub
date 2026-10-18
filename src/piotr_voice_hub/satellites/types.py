"""Satellite identity and playback target.

Copied from piotr_the_ai_server ai_server/microphones/types.py @ a299643.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class MicrophoneContext:
    type: str
    name: str
    area: str | None = None

    @property
    def instance_id(self) -> str:
        if self.area is None:
            return f"{self.type}:{self.name}"
        return f"{self.type}:{self.name}@{self.area}"


@dataclass(frozen=True)
class PlaybackTarget:
    type: str
    name: str
    address: str
    api_key: str
    expected_name: str | None = None
