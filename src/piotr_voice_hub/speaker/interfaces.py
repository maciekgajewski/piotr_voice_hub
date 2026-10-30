"""The speaker hint interface (T-018, design.md §4.1 step 5, §6)."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeakerMatch:
    """The top profile match at or above the service's threshold."""

    name: str
    confidence: float


class SpeakerHint(Protocol):
    async def recognize(self, pcm: bytes, request_id: str) -> SpeakerMatch | None:
        """The speaker of `pcm` (16 kHz, 16-bit, mono), or None: no match above the
        threshold, no profiles, or the service failed (logged). It is only a hint: this never
        raises for a service failure and returns within the configured timeout.
        `request_id` names the request in the logs."""
        ...
