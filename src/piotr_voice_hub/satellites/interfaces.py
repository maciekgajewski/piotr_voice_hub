"""Abstract satellite driver interface.

Copied from piotr_the_ai_server ai_server/microphones/interfaces.py @ a299643, without the
TextToSpeech protocol (TTS gets its own module in P1, design.md §5).
"""

from typing import Protocol

from piotr_voice_hub.satellites.messages import MicrophoneCommand, MicrophoneEvent
from piotr_voice_hub.satellites.types import MicrophoneContext, PlaybackTarget


class MicrophoneUnavailable(Exception):
    """Raised when a microphone is temporarily unreachable."""


class Microphone(Protocol):
    context: MicrophoneContext
    playback_target: PlaybackTarget

    async def wait_for_event(self) -> MicrophoneEvent:
        raise NotImplementedError

    async def send_output_event(self, event: MicrophoneCommand) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
