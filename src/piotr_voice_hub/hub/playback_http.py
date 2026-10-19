"""The shared playback HTTP server (T-014, design.md §4.1 step 6, §9).

One asyncio server on `playback_http.port` serves each playback as `GET /<playback_id>.wav`. The
satellite fetches it after `media_player_command(media_url, announcement=True)`. The body is a
live WAV stream: an "endless" header (the length is not known in advance), then the PCM chunks
as the hub hands them over. Ported from the old driver's `Box3PlaybackStream` (piotr_the_ai_server
@ a299643), which ran one `ThreadingHTTPServer` per playback on a random port.

Every wait on the device is bounded: reading the request, and each write. Audio is written in
slices of at most `SEND_SLICE_BYTES`, each with its own `SEND_TIMEOUT_SECONDS`, so a device that
reads at the audio rate never hits the limit, however big a chunk is (PR #16 re-review B1). A
device that stops reading mid-stream fills the socket buffers, and the next slice then blocks;
after `SEND_TIMEOUT_SECONDS` the connection is aborted (not closed: closing would flush to the
stalled peer) and the stream stays incomplete. The driver treats an incomplete stream like one
that was never fetched (T-014 review B1).

A stream holds at most `MAX_STREAM_SECONDS` of audio, in one of the supported formats. Each
stream serves one GET, and only to its satellite's IP. An unknown id, a second GET, a non-GET or
a request from another IP gets 404. At most `MAX_CONNECTIONS` requests from other addresses are
served at once; a satellite with an open stream is never refused.

Not bounded here: the writer's wait for the next chunk. The producer (the session, with TTS in
P1) must bound the time from `PlaybackBegin` to `PlaybackEnd` (docs/tasks/README.md, P1).
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

WAV_HEADER_BYTES = 44
WAV_STREAM_DATA_BYTES = 0x7FFFFFF0  # "endless": the real length is unknown while streaming
REQUEST_READ_TIMEOUT_SECONDS = 5.0
# One slice blocked this long: the device stopped reading. A playing device reads at least at
# the audio rate. One wait is set by asyncio's write-buffer watermarks, not the slice size:
# past 64 KiB, drain() waits until under 16 KiB, about 48 KiB of audio (about 1.1 s at
# 22050 Hz mono, 3 s at 8 kHz mono), so a healthy write stays well inside this.
SEND_TIMEOUT_SECONDS = 10.0
SEND_SLICE_BYTES = 16 * 1024
# Tight at 8 kHz mono: flushing a full 64 KiB buffer takes about 4 s (P1 carry-forward, T-017).
CLOSE_TIMEOUT_SECONDS = 5.0
MAX_STREAM_SECONDS = 300.0  # of audio buffered per stream; more fails loudly
MAX_CONNECTIONS = 8  # one GET per playback and at most a few satellites; more is not ours
# 16-bit PCM, mono or stereo, at the rates ESPHome's audio pipeline resamples from (Piper is
# 22050 Hz mono).
SUPPORTED_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000)
SUPPORTED_WIDTHS = (2,)
SUPPORTED_CHANNELS = (1, 2)

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
        client_ip: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate not in SUPPORTED_RATES or width not in SUPPORTED_WIDTHS:
            raise ValueError(f"unsupported playback format: {rate} Hz, {width * 8} bit")
        if channels not in SUPPORTED_CHANNELS:
            raise ValueError(f"unsupported playback format: {channels} channels")
        self.playback_id = playback_id
        self.client_ip = client_ip  # the one address allowed to GET it
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
        """The next chunk to send, or None at the end or when closed. Waits for the producer
        without a bound of its own: that bound is the producer's (module docstring)."""
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
        self._connections = 0

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

    def open_stream(
        self, playback_id: str, rate: int, width: int, channels: int, client_ip: str
    ) -> PlaybackStream:
        """Raises ValueError for an unsupported format."""
        assert playback_id not in self._streams, f"playback {playback_id} is already open"
        stream = PlaybackStream(playback_id, rate, width, channels, client_ip)
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
        satellite = peer[0] in {s.client_ip for s in self._streams.values()}
        if self._connections >= MAX_CONNECTIONS and not satellite:
            logger.warning("playback HTTP: %d connections; refused %s", self._connections, peer)
            writer.transport.abort()
            return
        self._connections += 1
        failed = False
        try:
            async with asyncio.timeout(REQUEST_READ_TIMEOUT_SECONDS):
                head = await reader.readuntil(b"\r\n\r\n")
            method, target, _ = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
            found = self._stream_for(method, target.split("?", 1)[0])
            if found is None or found.client_ip != peer[0]:  # another IP: as if unknown
                logger.warning(
                    "playback HTTP: %s %s from %s: not found%s",
                    method,
                    target,
                    peer,
                    "" if found is None else f" (only {found.client_ip} may fetch it)",
                )
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n")
                writer.write(b"Connection: close\r\n\r\n")
                await self._send(writer)
                return
            stream = found
            stream.requested.set()
            logger.info("playback %s requested by %s", stream.playback_id, peer)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\n")
            writer.write(b"Connection: close\r\n\r\n")
            writer.write(wav_header(stream.rate, stream.width, stream.channels))
            await self._send(writer)
            while (chunk := await stream.next_chunk()) is not None:
                if stream.first_audio_at is None:
                    stream.first_audio_at = stream.clock()
                for offset in range(0, len(chunk), SEND_SLICE_BYTES):
                    piece = chunk[offset : offset + SEND_SLICE_BYTES]
                    writer.write(piece)
                    await self._send(writer)  # the limit is per slice, not per chunk
                    stream.sent_bytes += len(piece)
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as error:
            # ValueError: a malformed request line; asyncio.LimitOverrunError is a ValueError
            # (a request head over 64 KiB). The driver's drain notices an incomplete stream.
            failed = True
            logger.warning("playback HTTP from %s failed: %r", peer, error)
        finally:
            self._connections -= 1
            if stream is not None:
                stream.done.set()
            await self._close(writer, abort=failed)

    def _stream_for(self, method: str, path: str) -> PlaybackStream | None:
        if method != "GET" or not path.startswith("/") or not path.endswith(".wav"):
            return None
        stream = self._streams.get(path[1:-4])
        if stream is None or stream.requested.is_set():
            return None  # unknown, released, or already fetched: one GET per stream
        return stream

    @staticmethod
    async def _close(writer: asyncio.StreamWriter, abort: bool) -> None:
        """Abort after an error: `close()` would first flush to a peer that may have stopped
        reading. Otherwise close (flushing the tail), and abort if that takes too long."""
        if abort:
            writer.transport.abort()
            return
        writer.close()
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await writer.wait_closed()
        except (OSError, TimeoutError):
            writer.transport.abort()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter) -> None:
        async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
            await writer.drain()
