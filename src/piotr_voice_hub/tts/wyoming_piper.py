"""Wyoming Piper TTS client (T-016, design.md §4.1 step 6, §5).

Ported from piotr_the_ai_server `PiperTextToSpeech.synthesize` (ai_server/microphones/tts.py @
a299643): one Wyoming connection per synthesis, one `synthesize` with the configured voice,
then `audio-start`, `audio-chunk`s and `audio-stop`. Changes from the old code:
- Every wait is bounded (connect, `info`, first audio, each next event); a bound that fires is
  a `TtsError` naming the wait. No retries.
- Strict events: an `error` event, EOF before `audio-stop`, a chunk before `audio-start` or in
  another format, or any other event type is a `TtsError` (the old code skipped them).
- A text cap: empty text or more than `MAX_TEXT_CHARS` is a `TtsTextError`, before connecting.
  design.md says nothing about long text; it is rejected, not split (T-016 plan).
- `start()` is only a reachability check (`describe` → `info` listing exactly the configured
  voice, which is all our service describes): the Piper service warms itself before it
  listens (T-015), so no validation synthesis.
- The connection is our own `asyncio.open_connection`; the `wyoming` package provides only
  the event framing and event classes.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event, async_read_event, async_write_event
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

from piotr_voice_hub.config import TtsConfig, parse_tcp_uri
from piotr_voice_hub.tts.interfaces import AudioFormat, TtsError, TtsTextError

CONNECT_TIMEOUT_SECONDS = 5.0
INFO_TIMEOUT_SECONDS = 5.0
# Warm, the service answers in 0.05-0.08 s; it serializes requests, so a concurrent one may
# wait for another's sentence or two.
FIRST_AUDIO_TIMEOUT_SECONDS = 10.0
# The longest gap is one sentence's synthesis (T-015: 0.14 s for 194 characters).
NEXT_EVENT_TIMEOUT_SECONDS = 10.0
CLOSE_TIMEOUT_SECONDS = 2.0
# About 65 s of audio (T-015: 191 characters gave 12.4 s) and about 1 s of synthesis; the
# playback stream holds up to 300 s. The service splits it into sentences.
MAX_TEXT_CHARS = 1000


def _parse[T](from_event: Callable[[Event], T], event: Event) -> T:
    """A typed event, or a TtsError for a malformed one."""
    try:
        return from_event(event)
    except (KeyError, TypeError, ValueError) as error:
        raise TtsError(f"malformed {event.type} event: {error!r}") from None


def check_text(text: str) -> str:
    """The text to send (stripped), or a TtsTextError."""
    stripped = text.strip()
    if not stripped:
        raise TtsTextError("empty text")
    if len(stripped) > MAX_TEXT_CHARS:
        raise TtsTextError(f"text is {len(stripped)} characters, the limit is {MAX_TEXT_CHARS}")
    return stripped


class _Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def send(self, event: Event, timeout: float) -> None:
        try:
            async with asyncio.timeout(timeout):
                await async_write_event(event, self.writer)
        except TimeoutError:
            raise TtsError(f"sending {event.type} took over {timeout:g} s") from None
        except OSError as error:
            raise TtsError(f"sending {event.type} failed: {error!r}") from error

    async def receive(self, timeout: float, waiting_for: str) -> Event:
        try:
            async with asyncio.timeout(timeout):
                event = await async_read_event(self.reader)
        except TimeoutError:
            raise TtsError(f"no {waiting_for} within {timeout:g} s") from None
        except (OSError, asyncio.IncompleteReadError) as error:
            raise TtsError(
                f"connection failed while waiting for {waiting_for}: {error!r}"
            ) from error
        if event is None:
            raise TtsError(f"connection closed (or a malformed event) before {waiting_for}")
        if Error.is_type(event.type):
            error = _parse(Error.from_event, event)
            raise TtsError(f"service error: {error.text} (code {error.code})")
        return event

    async def close(self) -> None:
        self.writer.close()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await self.writer.wait_closed()
        except (TimeoutError, OSError):
            self.writer.transport.abort()


class _PiperSpeech:
    def __init__(
        self,
        connection: _Connection,
        audio_format: AudioFormat,
        next_event_timeout: float,
    ) -> None:
        self.format = audio_format
        self._connection = connection
        self._next_event_timeout = next_event_timeout
        self.audio_bytes = 0
        self.ended = False

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[bytes]:
        while not self.ended:
            event = await self._connection.receive(self._next_event_timeout, "next audio event")
            if AudioStop.is_type(event.type):
                self.ended = True
                return
            if not AudioChunk.is_type(event.type):
                raise TtsError(f"unexpected {event.type} event while streaming audio")
            yield self._audio(_parse(AudioChunk.from_event, event))

    def _audio(self, chunk: AudioChunk) -> bytes:
        chunk_format = AudioFormat(chunk.rate, chunk.width, chunk.channels)
        if chunk_format != self.format:
            raise TtsError(
                f"audio chunk format {chunk_format} differs from the start {self.format}"
            )
        self.audio_bytes += len(chunk.audio)
        return chunk.audio


class WyomingPiperTts:
    """The hub's TTS client: one connection per synthesis to our Piper service (T-015)."""

    def __init__(
        self,
        config: TtsConfig,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        info_timeout: float = INFO_TIMEOUT_SECONDS,
        first_audio_timeout: float = FIRST_AUDIO_TIMEOUT_SECONDS,
        next_event_timeout: float = NEXT_EVENT_TIMEOUT_SECONDS,
    ) -> None:
        self._host, self._port = parse_tcp_uri(config.uri)
        self._voice = config.voice
        self._connect_timeout = connect_timeout
        self._info_timeout = info_timeout
        self._first_audio_timeout = first_audio_timeout
        self._next_event_timeout = next_event_timeout
        self._logger = logging.getLogger(f"{__name__}.WyomingPiperTts[{config.voice}]")

    async def _connect(self) -> _Connection:
        try:
            async with asyncio.timeout(self._connect_timeout):
                reader, writer = await asyncio.open_connection(self._host, self._port)
        except TimeoutError:
            raise TtsError(
                f"connect to {self._host}:{self._port} took over {self._connect_timeout:g} s"
            ) from None
        except OSError as error:
            raise TtsError(f"connect to {self._host}:{self._port} failed: {error!r}") from error
        return _Connection(reader, writer)

    async def start(self) -> None:
        """Startup check: the service answers `describe` and lists exactly the configured voice.

        Our service (T-015) describes only the one voice it serves (`advertise_only`), so a
        `tts.voice` other than that one fails here, before anything is armed."""
        start = time.monotonic()
        connection = await self._connect()
        try:
            await connection.send(Describe().event(), self._info_timeout)
            event = await connection.receive(self._info_timeout, "info")
        finally:
            await connection.close()
        if not Info.is_type(event.type):
            raise TtsError(f"unexpected {event.type} event in answer to describe")
        info = _parse(Info.from_event, event)
        listed = [v.name for p in info.tts for v in p.voices if v.installed]
        if listed != [self._voice]:
            raise TtsError(
                f"tts.voice is {self._voice!r}, but Piper at {self._host}:{self._port} "
                f"describes {listed} (it must describe exactly the configured voice)"
            )
        self._logger.info(
            "Piper reachable at %s:%d in %.3f s", self._host, self._port, time.monotonic() - start
        )

    @asynccontextmanager
    async def synthesize(self, text: str) -> AsyncIterator[_PiperSpeech]:
        """The format and PCM chunks of `text`; leaving the block closes the connection."""
        text = check_text(text)
        start = time.monotonic()
        connection = await self._connect()
        try:
            request = Synthesize(text=text, voice=SynthesizeVoice(name=self._voice))
            await connection.send(request.event(), self._first_audio_timeout)
            event = await connection.receive(self._first_audio_timeout, "audio-start")
            if not AudioStart.is_type(event.type):
                raise TtsError(f"expected audio-start, got {event.type}")
            audio_start = _parse(AudioStart.from_event, event)
            audio_format = AudioFormat(audio_start.rate, audio_start.width, audio_start.channels)
            if min(audio_format.rate, audio_format.width, audio_format.channels) <= 0:
                raise TtsError(f"invalid audio format {audio_format}")
            first_audio = time.monotonic() - start
            speech = _PiperSpeech(connection, audio_format, self._next_event_timeout)
            yield speech
            if speech.ended:
                rate = audio_format.rate * audio_format.width * audio_format.channels
                self._logger.info(
                    "synthesized %d characters: %.2f s of audio, audio-start after %.3f s, "
                    "done after %.3f s",
                    len(text),
                    speech.audio_bytes / rate,
                    first_audio,
                    time.monotonic() - start,
                )
        finally:
            await connection.close()
