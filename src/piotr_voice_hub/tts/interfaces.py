"""The TTS interface (T-016, design.md §5): the format, then the PCM chunks of one synthesis."""

from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AudioFormat:
    rate: int
    width: int
    channels: int


class TtsError(Exception):
    """The TTS service failed: unreachable, too slow, closed early, sent an `error`, or sent
    something unexpected. Fail loudly; there are no retries."""


class TtsUnavailableError(TtsError):
    """The service is not reachable: the connect was refused, timed out or failed, or the
    connection was reset. The hub's startup check waits for it (Piper may still be warming
    up); everywhere else it is a TtsError like any other."""


class TtsTextError(TtsError):
    """The text itself is rejected (empty, or over the cap) before anything is sent."""


class Speech(Protocol):
    """One synthesis in progress: its format, and its PCM chunks as they arrive."""

    format: AudioFormat

    def __aiter__(self) -> AsyncGenerator[bytes, None]: ...  # closable (contextlib.aclosing)


class TextToSpeech(Protocol):
    async def start(self) -> None:
        """Startup check that the service is reachable and serves the voice."""
        ...

    def synthesize(self, text: str) -> AbstractAsyncContextManager[Speech]: ...
