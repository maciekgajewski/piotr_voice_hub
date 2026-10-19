"""The shared playback HTTP server (T-014, design.md §4.1 step 6, §9).

One asyncio server on `playback_http.port` serves each playback as `GET /<playback_id>.wav`. The
satellite fetches it after `media_player_command(media_url, announcement=True)`. The body is a
live WAV stream: an "endless" header (the length is not known in advance), then the PCM chunks
as the hub hands them over. Ported from the old driver's `Box3PlaybackStream` (piotr_the_ai_server
@ a299643), which ran one `ThreadingHTTPServer` per playback on a random port.

Every wait is bounded: reading the request, and each write to a device that stopped reading.
A stream holds at most `MAX_STREAM_SECONDS` of audio. Each stream serves one GET; an unknown
id or a second GET gets 404.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable

WAV_HEADER_BYTES = 44
WAV_STREAM_DATA_BYTES = 0x7FFFFFF0  # "endless": the real length is unknown while streaming
REQUEST_READ_TIMEOUT_SECONDS = 5.0
SEND_TIMEOUT_SECONDS = 10.0  # one write blocked this long: the device stopped reading
CLOSE_TIMEOUT_SECONDS = 5.0
MAX_STREAM_SECONDS = 300.0  # of audio buffered per stream; more fails loudly

logger = logging.getLogger(__name__)


def wav_header(rate: int, width: int, channels: int) -> bytes:
    """A 44-byte PCM WAV header with the data size set to `WAV_STREAM_DATA_BYTES` (old repo)."""
    data_size = WAV_STREAM_DATA_BYTES
    return (
        b"RIFF"
        + (data_size + WAV_HEADER_BYTES - 8).to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # PCM
        + channels.to_bytes(2, "little")
        + rate.to_bytes(4, "little")
        + (rate * channels * width).to_bytes(4, "little")
        + (channels * width).to_bytes(2, "little")
        + (width * 8).to_bytes(2, "little")
        + b"data"
        + data_size.to_bytes(4, "little")
    )


class StreamFull(Exception):
    """The stream already holds `MAX_STREAM_SECONDS` of audio."""


class PlaybackStream:
    """One playback's audio, written by the driver and sent to the one GET for it."""

    def __init__(
        self,
        playback_id: str,
        rate: int,
        width: int,
        channels: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        assert rate > 0 and width > 0 and channels > 0, (rate, width, channels)
        self.playback_id = playback_id
        self.rate, self.width, self.channels = rate, width, channels
        self.requested = asyncio.Event()  # the device sent its GET
        self.done = asyncio.Event()  # the GET finished (all sent, failed) or the stream closed
        self.queued_bytes = 0
        self.sent_bytes = 0
        self.first_audio_at: float | None = None  # when the first chunk was written (old repo)
        self.clock = clock
        self._chunks: deque[bytes] = deque()
        self._wakeup = asyncio.Event()
        self._ended = False
        self._closed = False

    @property
    def byte_rate(self) -> int:
        return self.rate * self.width * self.channels

    @property
    def audio_seconds(self) -> float:
        return self.queued_bytes / self.byte_rate

    @property
    def complete(self) -> bool:
        """Ended, and every queued byte was sent."""
        return self._ended and self.sent_bytes == self.queued_bytes

    def write(self, data: bytes) -> None:
        assert not self._ended, "write after end"
        if self._closed:
            return
        if self.queued_bytes + len(data) > MAX_STREAM_SECONDS * self.byte_rate:
            raise StreamFull(f"playback {self.playback_id} exceeds {MAX_STREAM_SECONDS:g} s")
        self._chunks.append(data)
        self.queued_bytes += len(data)
        self._wakeup.set()

    def end(self) -> None:
        self._ended = True
        self._wakeup.set()

    def close(self) -> None:
        """Abort: the GET (if any) stops sending; nothing more is written."""
        self._closed = True
        self._chunks.clear()
        self._wakeup.set()
        if not self.requested.is_set():
            self.done.set()

    def remaining_seconds(self) -> float:
        """Audio still to play on the device: its length minus the time since the first chunk
        went out (old repo; the driver adds its grace). Negative once it should have ended;
        0 if nothing was sent."""
        if self.first_audio_at is None:
            return 0.0
        return self.audio_seconds - (self.clock() - self.first_audio_at)

    async def next_chunk(self) -> bytes | None:
        """The next chunk to send, or None at the end or when closed."""
        while not self._chunks:
            if self._ended or self._closed:
                return None
            self._wakeup.clear()
            await self._wakeup.wait()
        if self._closed:
            return None
        return self._chunks.popleft()


class PlaybackHttpServer:
    """Created and closed by main; the drivers open one stream per playback."""

    def __init__(self, host: str, port: int) -> None:
        self._host, self._requested_port = host, port
        self._server: asyncio.Server | None = None
        self._streams: dict[str, PlaybackStream] = {}

    @property
    def port(self) -> int:
        """The bound port (differs from the configured one only when that is 0, in tests)."""
        assert self._server is not None, "not started"
        return self._server.sockets[0].getsockname()[1]

    @staticmethod
    def path(playback_id: str) -> str:
        return f"/{playback_id}.wav"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self._host, self._requested_port)
        logger.info("playback HTTP listening on %s:%d", self._host, self.port)

    def open_stream(self, playback_id: str, rate: int, width: int, channels: int) -> PlaybackStream:
        assert playback_id not in self._streams, f"playback {playback_id} is already open"
        stream = PlaybackStream(playback_id, rate, width, channels)
        self._streams[playback_id] = stream
        return stream

    def release(self, stream: PlaybackStream) -> None:
        """Close the stream and forget it: a later GET for it gets 404."""
        stream.close()
        if self._streams.get(stream.playback_id) is stream:
            del self._streams[stream.playback_id]

    async def close(self) -> None:
        for stream in list(self._streams.values()):
            self.release(stream)
        if self._server is None:
            return
        self._server.close()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await self._server.wait_closed()
        except TimeoutError:
            logger.error("playback HTTP did not close within %g s", CLOSE_TIMEOUT_SECONDS)
        self._server = None
        logger.info("playback HTTP closed")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        stream: PlaybackStream | None = None
        try:
            async with asyncio.timeout(REQUEST_READ_TIMEOUT_SECONDS):
                head = await reader.readuntil(b"\r\n\r\n")
            method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
            stream = self._stream_for(method, target.split("?", 1)[0])
            if stream is None:
                logger.warning("playback HTTP: %s %s from %s: not found", method, target, peer)
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n")
                writer.write(b"Connection: close\r\n\r\n")
                await self._send(writer)
                return
            stream.requested.set()
            logger.info("playback %s requested by %s", stream.playback_id, peer)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\n")
            writer.write(b"Connection: close\r\n\r\n")
            writer.write(wav_header(stream.rate, stream.width, stream.channels))
            await self._send(writer)
            while (chunk := await stream.next_chunk()) is not None:
                if stream.first_audio_at is None:
                    stream.first_audio_at = stream.clock()
                writer.write(chunk)
                await self._send(writer)
                stream.sent_bytes += len(chunk)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as error:
            # ValueError: a malformed request line; asyncio.LimitOverrunError is a ValueError
            # (a request head over 64 KiB). The driver's drain notices an incomplete stream.
            logger.warning("playback HTTP from %s failed: %r", peer, error)
        finally:
            if stream is not None:
                stream.done.set()
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                    await writer.wait_closed()

    def _stream_for(self, method: str, path: str) -> PlaybackStream | None:
        if method != "GET" or not path.startswith("/") or not path.endswith(".wav"):
            return None
        stream = self._streams.get(path[1:-4])
        if stream is None or stream.requested.is_set():
            return None  # unknown, released, or already fetched: one GET per stream
        return stream

    @staticmethod
    async def _send(writer: asyncio.StreamWriter) -> None:
        async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
            await writer.drain()
