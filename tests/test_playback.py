"""T-014: the shared playback HTTP server and ESPHome playback and cue responses (no device:
a fake client whose media player fetches the announced URL over loopback)."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from aioesphomeapi.model import MediaPlayerCommand, MediaPlayerState, SupportsResponseType
from microphone_driver_conformance import assert_playback_contract
from test_esphome_driver import ALL_SERVICES, Checked, Clients, FakeClient, Response, arm, make

from piotr_voice_hub.hub import playback_http
from piotr_voice_hub.hub.playback_http import PlaybackHttpServer, PlaybackStream, wav_header
from piotr_voice_hub.satellites import esphome
from piotr_voice_hub.satellites.interfaces import MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    CueFinished,
    CueType,
    PlaybackBegin,
    PlaybackChunk,
    PlaybackEnd,
    PlaybackFinished,
    PlaybackStop,
    PlayCue,
    SetVisualState,
    VisualState,
)
from piotr_voice_hub.satellites.protocol import MicrophoneProtocolState

RATE, WIDTH, CHANNELS = 22050, 2, 1
BYTE_RATE = RATE * WIDTH * CHANNELS
LOOPBACK = "127.0.0.1"
OK_HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nConnection: close\r\n\r\n"


@asynccontextmanager
async def serving() -> AsyncIterator[PlaybackHttpServer]:
    server = PlaybackHttpServer("127.0.0.1", 0)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


async def get(server: PlaybackHttpServer, request: bytes, source: str = LOOPBACK) -> bytes:
    """`source`: any 127.x.y.z, to stand in for another LAN address (Linux loopback)."""
    reader, writer = await asyncio.open_connection(LOOPBACK, server.port, local_addr=(source, 0))
    writer.write(request)
    try:
        return await asyncio.wait_for(reader.read(), 2.0)
    finally:
        writer.close()


async def get_wav(server: PlaybackHttpServer, playback_id: str, source: str = LOOPBACK) -> bytes:
    request = f"GET /{playback_id}.wav HTTP/1.1\r\nHost: x\r\n\r\n".encode()
    return await get(server, request, source)


def run(scenario: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(asyncio.wait_for(scenario(), 10.0))  # type: ignore[arg-type]


# Server


def test_wav_header_is_a_44_byte_pcm_header_with_an_endless_length() -> None:
    header = wav_header(22050, 2, 1)
    assert len(header) == 44 and header[:4] == b"RIFF" and header[8:16] == b"WAVEfmt "
    assert int.from_bytes(header[22:24], "little") == 1  # channels
    assert int.from_bytes(header[24:28], "little") == 22050
    assert int.from_bytes(header[28:32], "little") == 44100  # byte rate
    assert int.from_bytes(header[34:36], "little") == 16  # bits
    assert header[36:40] == b"data" and int.from_bytes(header[40:44], "little") == 0x7FFFFFF0


def test_a_get_streams_the_header_then_chunks_written_before_and_after_it() -> None:
    async def scenario() -> None:
        async with serving() as server:
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK)
            stream.write(b"early")
            body = asyncio.create_task(get_wav(server, "pb-1"))
            await asyncio.wait_for(stream.requested.wait(), 2.0)
            stream.write(b"late")
            stream.end()
            assert await body == OK_HEAD + wav_header(RATE, WIDTH, CHANNELS) + b"earlylate"
            await asyncio.wait_for(stream.done.wait(), 2.0)
            assert stream.complete and stream.sent_bytes == stream.queued_bytes == 9

    run(scenario)


def test_unknown_second_and_released_gets_are_404() -> None:
    async def scenario() -> None:
        async with serving() as server:
            assert (await get_wav(server, "nope")).startswith(b"HTTP/1.1 404 Not Found\r\n")
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK)
            first = asyncio.create_task(get_wav(server, "pb-1"))
            await asyncio.wait_for(stream.requested.wait(), 2.0)
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")  # one GET only
            server.release(stream)
            assert (await first).startswith(OK_HEAD)  # aborted: ends without more data
            assert not stream.complete
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")
            post = b"POST /x.wav HTTP/1.1\r\n\r\n"
            assert (await get(server, post)).startswith(b"HTTP/1.1 404")

    run(scenario)


def test_a_malformed_or_silent_request_is_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(playback_http, "REQUEST_READ_TIMEOUT_SECONDS", 0.1)

    async def scenario() -> None:
        async with serving() as server:
            assert await get(server, b"garbage\r\n\r\n") == b""
            reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
            assert await asyncio.wait_for(reader.read(), 2.0) == b""  # closed after 0.1 s
            writer.close()

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert caplog.text.count("playback HTTP from") == 2


def test_a_stream_holds_at_most_max_stream_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playback_http, "MAX_STREAM_SECONDS", 1.0)

    async def scenario() -> None:
        stream = PlaybackStream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK)
        stream.write(bytes(BYTE_RATE))
        with pytest.raises(playback_http.StreamFull, match="exceeds 1 s"):
            stream.write(b"\0\0")

    run(scenario)


def test_remaining_seconds_counts_from_the_first_chunk_sent() -> None:
    async def scenario() -> None:
        now = [100.0]
        stream = PlaybackStream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK, clock=lambda: now[0])
        stream.write(bytes(2 * BYTE_RATE))
        assert stream.audio_seconds == 2.0 and stream.remaining_seconds() == 0.0  # not sent
        stream.first_audio_at = 100.0
        now[0] = 100.5
        assert stream.remaining_seconds() == 1.5
        now[0] = 103.0
        assert stream.remaining_seconds() == -1.0

    run(scenario)


def test_the_close_timeout_covers_a_full_buffer_at_the_stream_rate() -> None:
    assert playback_http.close_timeout(None) == 5.0  # a 404 or a failed request
    assert playback_http.close_timeout(44100) == 5.0  # Piper: 64 KiB is 1.5 s
    assert playback_http.close_timeout(16000) == pytest.approx(65536 / 16000 + 2.0)  # 6.1 s


def test_a_get_closes_with_its_stream_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    rates: list[int | None] = []

    def recording(byte_rate: int | None) -> float:
        rates.append(byte_rate)
        return 5.0

    monkeypatch.setattr(playback_http, "close_timeout", recording)

    async def scenario() -> None:
        async with serving() as server:
            stream = server.open_stream("pb-1", 8000, WIDTH, CHANNELS, LOOPBACK)
            stream.end()
            await get_wav(server, "pb-1")
            await get(server, b"GET /unknown.wav HTTP/1.1\r\n\r\n")
            await asyncio.sleep(0.05)

    run(scenario)
    assert rates == [16000, None, 16000]  # the GET, the 404, then the server's own close


def test_close_ends_an_open_get_and_unblocks_its_done() -> None:
    async def scenario() -> None:
        server = PlaybackHttpServer("127.0.0.1", 0)
        await server.start()
        stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK)
        body = asyncio.create_task(get_wav(server, "pb-1"))
        await asyncio.wait_for(stream.requested.wait(), 2.0)
        await server.close()
        assert (await body).startswith(OK_HEAD)
        assert stream.done.is_set() and not stream.complete

    run(scenario)


# Driver playback


async def playing(
    server: PlaybackHttpServer, local_ip: esphome.LocalIpFor = esphome.local_ip_for
) -> tuple[Checked, Clients]:
    mic, clients = make(playback_server=server, local_ip=local_ip)
    await mic.send_output_event(SetVisualState(VisualState.IDLE))  # connect
    return mic, clients


def test_passes_playback_contract_over_http() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await assert_playback_contract(mic)
            assert clients.last.media_commands == [
                {
                    "media_url": f"http://127.0.0.1:{server.port}/playback-1.wav",
                    "announcement": True,
                }
            ]
            assert clients.last.fetched == [OK_HEAD + wav_header(22050, 2, 1) + b"audio"]

    run(scenario)


def test_playback_finished_waits_for_the_audio_to_play_out_plus_the_grace() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS, volume=0.5))
            started = time.monotonic()
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE // 2)))  # 0.5 s
            await mic.send_output_event(PlaybackEnd("pb-1"))  # returns at once
            assert time.monotonic() - started < 0.2
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")
            assert time.monotonic() - started >= 0.5 + esphome.PLAYBACK_DRAIN_GRACE_SECONDS
            assert clients.last.media_commands[0] == {"volume": 0.5}  # then the announcement
            assert clients.last.media_commands[1]["announcement"] is True
            assert len(clients.last.fetched[0]) == len(OK_HEAD) + 44 + BYTE_RATE // 2

    run(scenario)


@pytest.mark.parametrize(
    ("connected", "route_to"), [("10.0.0.9", "10.0.0.9"), ("::ffff:10.0.0.8", "10.0.0.8")]
)
def test_the_url_uses_the_local_ipv4_on_the_route_to_the_connected_address(
    connected: str, route_to: str
) -> None:
    async def scenario() -> None:
        async with serving() as server:
            asked: list[str] = []

            def local_ip(device_ip: str) -> str:
                asked.append(device_ip)
                return "192.168.1.2"

            mic, clients = make(playback_server=server, local_ip=local_ip)

            def setup(client: FakeClient) -> None:
                client.connected_address = connected
                client.fetch = False  # 192.168.1.2 is not ours to connect to

            clients.setup = setup
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            assert asked == [route_to]
            url = clients.last.media_commands[0]["media_url"]
            assert url == f"http://192.168.1.2:{server.port}/pb-1.wav"
            await mic.close()

    run(scenario)


def test_an_ipv6_connection_cannot_play_and_a_missing_address_fails_loudly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = make(playback_server=server)
            clients.setup = lambda client: setattr(client, "connected_address", "fe80::1")
            with pytest.raises(MicrophoneUnavailable, match="not IPv4"):
                await mic.driver.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            assert "connected over fe80::1, not IPv4" in caplog.text
            assert clients.last.media_commands == []
            mic, clients = make(playback_server=server)
            clients.setup = lambda client: setattr(client, "connected_address", None)
            with pytest.raises(RuntimeError, match="no connected_address"):  # no DNS fallback
                await mic.driver.send_output_event(SetVisualState(VisualState.IDLE))

    with caplog.at_level(logging.WARNING):
        run(scenario)


def test_a_device_that_never_fetches_makes_the_driver_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(esphome, "STREAM_REQUEST_TIMEOUT_SECONDS", 0.1)

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            clients.last.fetch = False
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", b"audio"))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            with pytest.raises(MicrophoneUnavailable, match="did not fetch playback pb-1"):
                await mic.driver.wait_for_event()
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")  # released

    run(scenario)


def test_close_during_playback_cancels_it_and_releases_the_stream() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            clients.last.fetch = False
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", b"audio"))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            await mic.close()
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")
            await asyncio.sleep(0.1)
            assert mic.driver._events.empty()  # no PlaybackFinished after close

    run(scenario)


STOP = {"command": MediaPlayerCommand.STOP, "announcement": True}


@pytest.mark.parametrize("ended", [False, True])
def test_playback_stop_stops_the_device_and_releases_the_stream(ended: bool) -> None:
    """T-019: STOP to the announcement pipeline, the GET ends, a later GET is 404, then one
    PlaybackFinished once the player is idle; the drain never emits a second one."""

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            client = clients.last
            client.url_states = True  # PLAYING while fetching, IDLE after the STOP
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE)))  # 1 s
            if ended:
                await mic.send_output_event(PlaybackEnd("pb-1"))
            await asyncio.sleep(0.2)  # fetched and playing
            started = time.monotonic()
            await mic.send_output_event(PlaybackStop("pb-1"))
            assert time.monotonic() - started < 0.5
            assert client.media_commands[-1] == STOP
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")
            await asyncio.sleep(1.2)  # past the audio's own end
            assert mic.driver._events.empty()
            assert len(client.fetched) == 1  # the GET ended when the stream was released

    run(scenario)


def test_playback_stop_waits_for_a_busy_player_bounded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(esphome, "PLAYER_FREE_TIMEOUT_SECONDS", 0.2)

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            client = clients.last
            client.fetch = False
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await asyncio.sleep(0.05)  # the initial IDLE is in
            await client.player(MediaPlayerState.PLAYING)  # and it never reports IDLE
            started = time.monotonic()
            await mic.send_output_event(PlaybackStop("pb-1"))
            assert time.monotonic() - started >= 0.2
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")
            assert "still busy 0.2 s after the stop" in caplog.text

    with caplog.at_level(logging.WARNING):
        run(scenario)


def test_playback_stop_after_the_drain_finished_does_nothing() -> None:
    """The session may stop a playback whose PlaybackFinished is queued but not yet read."""

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", b"audio"))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            while mic.driver._playback is not None:  # bounded by run()
                await asyncio.sleep(0.01)
            await mic.send_output_event(PlaybackStop("pb-1"))
            assert STOP not in clients.last.media_commands
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")
            assert mic.driver._events.empty()

    run(scenario)


def test_playback_stop_on_an_unavailable_driver_raises() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            clients.last.fetch = False
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            on_stop = clients.last.on_stop
            assert on_stop is not None
            await on_stop(False)
            with pytest.raises(MicrophoneUnavailable, match="transport stopped"):
                await mic.driver.send_output_event(PlaybackStop("pb-1"))
            assert STOP not in clients.last.media_commands

    run(scenario)


def test_a_stream_over_the_limit_makes_the_driver_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(playback_http, "MAX_STREAM_SECONDS", 0.001)

    async def scenario() -> None:
        async with serving() as server:
            mic, _ = await playing(server)
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            with pytest.raises(MicrophoneUnavailable, match="exceeds"):
                await mic.driver.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE)))
            with pytest.raises(MicrophoneUnavailable, match="exceeds"):
                await mic.driver.wait_for_event()

    run(scenario)


def test_playback_fails_loudly_without_a_media_player_or_a_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = make(playback_server=server)
            clients.setup = lambda client: setattr(client, "entities", [])
            with pytest.raises(RuntimeError, match="has no media player"):
                await mic.driver.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            assert "firmware exposes no media player" in caplog.text
        mic, _ = make()  # the wake-test runner: no playback server
        with pytest.raises(RuntimeError, match="no playback HTTP server"):
            await mic.driver.send_output_event(PlaybackBegin("pb-2", RATE, WIDTH, CHANNELS))

    with caplog.at_level(logging.WARNING):
        run(scenario)


def test_playback_only_while_disarmed() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, _ = await playing(server)
            await arm(mic)
            with pytest.raises(AssertionError, match="MP-AUDIO-001"):
                await mic.driver.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))

    run(scenario)


def test_another_ip_gets_404_like_an_unknown_id_and_the_satellite_can_still_fetch() -> None:
    async def scenario() -> None:
        async with serving() as server:
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, "127.0.0.2")
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404 Not Found\r\n")
            assert not stream.requested.is_set()
            body = asyncio.create_task(get_wav(server, "pb-1", source="127.0.0.2"))
            await asyncio.wait_for(stream.requested.wait(), 2.0)
            stream.end()
            assert (await body).startswith(OK_HEAD)

    run(scenario)


def test_connections_over_the_cap_are_closed_at_once_except_for_a_satellite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(playback_http, "MAX_CONNECTIONS", 1)

    async def refused(source: str) -> None:
        try:  # closed without a response (aborted: a reset)
            assert await get_wav(server, "nope", source) == b""
        except ConnectionResetError:
            pass

    async def scenario() -> None:
        nonlocal server
        async with serving() as server:
            _, idle = await asyncio.open_connection(  # holds the one slot
                LOOPBACK, server.port, local_addr=("127.0.0.3", 0)
            )
            await asyncio.sleep(0.05)
            await refused("127.0.0.4")
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, "127.0.0.2")
            body = asyncio.create_task(get_wav(server, "pb-1", source="127.0.0.2"))
            await asyncio.wait_for(stream.requested.wait(), 2.0)  # served despite the cap
            stream.end()
            assert (await body).startswith(OK_HEAD)
            await refused("127.0.0.4")
            idle.close()
            await asyncio.sleep(0.05)
            assert (await get_wav(server, "nope")).startswith(b"HTTP/1.1 404")  # slot is free

    server = PlaybackHttpServer(LOOPBACK, 0)
    run(scenario)


class RateWriter:
    """A StreamWriter whose peer reads at exactly `byte_rate` (drain takes as long as the
    buffered audio), or never (`stalled`: drain and a flushing close hang until aborted)."""

    def __init__(self, byte_rate: float, stalled: bool = False) -> None:
        self.byte_rate, self.stalled = byte_rate, stalled
        self.data = bytearray()
        self.pending = 0
        self.aborted = False
        self.transport = self

    def get_extra_info(self, name: str) -> object:
        assert name == "peername"
        return (LOOPBACK, 40000)

    def write(self, data: bytes) -> None:
        self.data += data
        self.pending += len(data)

    async def drain(self) -> None:
        if self.stalled:
            await asyncio.Event().wait()
        await asyncio.sleep(self.pending / self.byte_rate)
        self.pending = 0

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        if self.stalled:
            await asyncio.Event().wait()  # flushing to a peer that never reads

    def abort(self) -> None:
        self.aborted = True


async def handle(server: PlaybackHttpServer, writer: RateWriter) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"GET /pb-1.wav HTTP/1.1\r\nHost: x\r\n\r\n")
    await server._handle(reader, writer)  # type: ignore[arg-type]


@pytest.mark.parametrize(("slice_bytes", "complete"), [(1024, True), (1 << 20, False)])
def test_a_reader_at_exactly_the_audio_rate_gets_one_big_chunk(
    monkeypatch: pytest.MonkeyPatch, slice_bytes: int, complete: bool
) -> None:
    """PR #16 re-review B1, scaled: a 2 s chunk with a 0.3 s write limit. Sliced (1 KiB, 64 ms
    of audio each) it is all sent; written whole (the old code) it is cut off."""
    monkeypatch.setattr(playback_http, "SEND_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(playback_http, "SEND_SLICE_BYTES", slice_bytes)

    async def scenario() -> None:
        server = PlaybackHttpServer(LOOPBACK, 0)
        stream = server.open_stream("pb-1", 8000, 2, 1, LOOPBACK)  # 16000 bytes/s
        chunk = bytes(range(256)) * 125  # 32000 bytes: 2 s of audio
        stream.write(chunk)
        stream.end()
        writer = RateWriter(byte_rate=16000)
        started = time.monotonic()
        await handle(server, writer)
        assert stream.complete is complete
        if complete:
            assert bytes(writer.data).endswith(wav_header(8000, 2, 1) + chunk)
            assert 1.9 < time.monotonic() - started < 3.0  # paced by the reader
            assert not writer.aborted
        else:
            assert writer.aborted and stream.sent_bytes == 0

    run(scenario)


def test_a_stalled_reader_is_aborted_and_the_handler_does_not_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(playback_http, "SEND_TIMEOUT_SECONDS", 0.2)

    async def scenario() -> None:
        server = PlaybackHttpServer(LOOPBACK, 0)
        stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS, LOOPBACK)
        stream.write(bytes(BYTE_RATE))
        writer = RateWriter(byte_rate=1, stalled=True)
        started = time.monotonic()
        await handle(server, writer)  # close() + wait_closed() would hang here
        assert time.monotonic() - started < 1.0
        assert writer.aborted and stream.done.is_set() and not stream.complete

    run(scenario)


@pytest.mark.parametrize(
    ("rate", "width", "channels", "message"),
    [(44100, 3, 1, "44100 Hz, 24 bit"), (12345, 2, 1, "12345 Hz"), (16000, 2, 3, "3 channels")],
)
def test_unsupported_formats_are_rejected_before_anything_is_sent(
    rate: int, width: int, channels: int, message: str
) -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            with pytest.raises(ValueError, match=message):
                await mic.driver.send_output_event(PlaybackBegin("pb-1", rate, width, channels))
            assert clients.last.media_commands == []
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")

    run(scenario)


async def unavailable(mic: Checked, match: str) -> None:
    with pytest.raises(MicrophoneUnavailable, match=match):
        await asyncio.wait_for(mic.driver.wait_for_event(), 10.0)
    queued = list(mic.driver._events._queue)  # type: ignore[attr-defined]
    assert not [event for event in queued if isinstance(event, PlaybackFinished)]


def test_a_reader_that_drops_mid_stream_makes_the_driver_unavailable() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            clients.last.fetch_mode, clients.last.fetch_bytes = "drop", len(OK_HEAD) + 44 + 100
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(1000)))
            await asyncio.sleep(0.2)  # fetched 100 bytes of audio, then dropped
            for _ in range(5):  # writes to the dropped connection fail
                await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE)))
                await asyncio.sleep(0.02)
            await mic.send_output_event(PlaybackEnd("pb-1"))
            await unavailable(mic, "device got .* bytes of playback pb-1")

    run(scenario)


def test_a_reader_that_stalls_mid_stream_makes_the_driver_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(playback_http, "SEND_TIMEOUT_SECONDS", 0.3)
    rate, channels = 48000, 2  # 192 kB/s: 30 MB is more than the loopback socket buffers

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            clients.last.fetch_mode, clients.last.fetch_bytes = "stall", len(OK_HEAD) + 44 + 100
            await mic.send_output_event(PlaybackBegin("pb-1", rate, WIDTH, channels))
            for _ in range(30):
                await mic.send_output_event(PlaybackChunk("pb-1", bytes(1_000_000)))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            started = time.monotonic()
            await unavailable(mic, "device got .* bytes of playback pb-1")
            assert time.monotonic() - started < 5.0  # the write timeout, not 30 s + audio
            for writer in clients.last.stalled:
                writer.close()

    run(scenario)


def test_a_transport_stop_during_the_drain_is_unavailable_with_no_playback_finished() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE)))  # 1 s
            await mic.send_output_event(PlaybackEnd("pb-1"))
            await asyncio.sleep(0.3)  # fetched; the drain waits for the audio to play out
            on_stop = clients.last.on_stop
            assert on_stop is not None
            await on_stop(False)
            await unavailable(mic, "transport stopped")
            await asyncio.sleep(1.0)  # past the estimated end
            queued = list(mic.driver._events._queue)  # type: ignore[attr-defined]
            assert not [event for event in queued if isinstance(event, PlaybackFinished)]
            assert (await get_wav(server, "pb-1")).startswith(b"HTTP/1.1 404")

    run(scenario)


def test_an_unexpected_drain_error_is_raised_to_the_reader(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(self: PlaybackStream) -> float:
        raise ZeroDivisionError("a bug")

    monkeypatch.setattr(PlaybackStream, "remaining_seconds", broken)

    async def scenario() -> None:
        async with serving() as server:
            mic, _ = await playing(server)
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", b"audio"))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            with pytest.raises(ZeroDivisionError, match="a bug"):
                await asyncio.wait_for(mic.driver.wait_for_event(), 5.0)
            with pytest.raises(MicrophoneUnavailable, match="playback drain failed"):
                await mic.driver.send_output_event(SetVisualState(VisualState.IDLE))

    with caplog.at_level(logging.ERROR):
        run(scenario)
    assert "playback pb-1 drain failed" in caplog.text


def test_playback_begin_waits_for_a_pending_stray_stream_stop() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await clients.last.handlers["start"]("conversation", 0, None, None)  # while disarmed
            assert mic.driver._stale_stop is not None
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            assert mic.driver._stale_stop is None
            assert clients.last.calls[-1] == "stop_listening"  # stopped before the announce
            assert clients.last.media_commands[0]["announcement"] is True
            await mic.close()

    run(scenario)


# Cues with a firmware response (T-014 carry-forward; the firmware side is P2)


@pytest.mark.parametrize(
    ("supports", "return_response"),
    [(SupportsResponseType.OPTIONAL, True), (SupportsResponseType.STATUS, False)],
)
def test_a_cue_service_with_supports_response_finishes_when_the_firmware_answers(
    supports: SupportsResponseType, return_response: bool
) -> None:
    async def scenario() -> None:
        mic, clients = make()

        def setup(client: FakeClient) -> None:
            client.responding.update({"play_message_end_cue": supports})
            client.response_delay = 0.3

        clients.setup = setup
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert time.monotonic() - started >= 0.3  # waited for the cue to play
        assert await mic.wait_for_event() == CueFinished("cue-1")
        assert clients.last.call_kwargs == [
            {"return_response": return_response, "timeout": esphome.CUE_RESPONSE_TIMEOUT_SECONDS}
        ]

    run(scenario)


def test_a_cue_without_supports_response_finishes_once_its_announcement_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        mic, clients = make()
        clients.setup = lambda client: setattr(client, "cue_seconds", 0.3)
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.TIMEOUT))
        assert time.monotonic() - started >= 0.3  # PLAYING, then IDLE (T-017 fix)
        assert await mic.wait_for_event() == CueFinished("cue-1")
        assert clients.last.call_kwargs == [{}]

    with caplog.at_level(logging.INFO):
        run(scenario)
    assert "cue services without supports_response" in caplog.text
    names = [n for n in ALL_SERVICES if n.startswith("play_")]
    assert str(names) in caplog.text
    assert "going on" not in caplog.text


def test_a_cue_whose_announcing_update_is_merged_into_idle_still_finishes_at_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """On hardware (T-017 fix), a short cue's ANNOUNCING and IDLE reached the API as one IDLE."""

    async def scenario() -> None:
        mic, clients = make()
        clients.setup = lambda client: setattr(client, "cue_merged", True)
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        elapsed = time.monotonic() - started  # the floor, not the 1 s start bound
        assert esphome.CUE_MIN_SECONDS <= elapsed < esphome.CUE_MIN_SECONDS + 0.3
        assert await mic.wait_for_event() == CueFinished("cue-1")

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "going on" not in caplog.text


def test_a_cue_that_never_announces_finishes_after_the_start_bound(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(esphome, "CUE_START_TIMEOUT_SECONDS", 0.1)

    async def scenario() -> None:
        mic, clients = make()
        clients.setup = lambda client: setattr(client, "cue_announces", False)
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert time.monotonic() - started >= 0.1
        assert await mic.wait_for_event() == CueFinished("cue-1")  # not unavailable

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "cue play_message_end_cue: the media player reported no change" in caplog.text


def test_a_cue_that_announces_too_long_finishes_after_the_end_bound(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(esphome, "CUE_END_TIMEOUT_SECONDS", 0.1)

    async def scenario() -> None:
        mic, clients = make()
        clients.setup = lambda client: setattr(client, "cue_seconds", 5.0)
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert time.monotonic() - started < 1.0
        assert await mic.wait_for_event() == CueFinished("cue-1")

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "cue play_message_end_cue: the media player is still busy after" in caplog.text


def test_playback_sends_its_url_only_once_the_player_stops_announcing() -> None:
    """The hardware race (T-017 fix): a Voice PE drops a URL sent while a cue announces."""

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            client = clients.last
            await asyncio.sleep(0.05)  # the initial IDLE state has arrived
            await client.player(MediaPlayerState.ANNOUNCING)
            begin = asyncio.create_task(
                mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            )
            await asyncio.sleep(0.2)
            assert not begin.done() and client.media_commands == []
            await client.player(MediaPlayerState.IDLE)
            await begin
            assert client.media_commands[0]["announcement"] is True
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE // 10)))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")

    run(scenario)


def test_playback_sends_its_url_anyway_after_the_player_free_bound(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(esphome, "PLAYER_FREE_TIMEOUT_SECONDS", 0.1)

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            await asyncio.sleep(0.05)
            await clients.last.player(MediaPlayerState.PLAYING)  # and it stays so
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            assert clients.last.media_commands[0]["announcement"] is True
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE // 10)))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "playback pb-1: the media player is still busy after 0.1 s" in caplog.text


def test_a_disconnect_while_waiting_for_the_player_is_unavailable_at_once() -> None:
    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            client = clients.last
            await asyncio.sleep(0.05)
            await client.player(MediaPlayerState.ANNOUNCING)
            begin = asyncio.create_task(
                mic.driver.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            )
            await asyncio.sleep(0.05)
            assert client.on_stop is not None
            await client.on_stop(False)
            with pytest.raises(MicrophoneUnavailable, match="transport stopped"):
                await asyncio.wait_for(begin, 1.0)
            assert client.media_commands == []

    run(scenario)


def test_an_unrelated_update_right_after_the_cue_call_does_not_finish_the_cue() -> None:
    """PR #25 review S1: an IDLE (the dial) 5 ms after the call counts as an update, but the
    cue is not finished before CUE_MIN_SECONDS, nor while its own PLAYING lasts."""

    async def scenario(cue_end: float, earliest: float) -> None:
        mic, clients = make()

        def setup(client: FakeClient) -> None:
            client.extra_update_after = 0.005
            client.cue_start_seconds = 0.05
            client.cue_seconds = cue_end - 0.05

        clients.setup = setup
        await mic.send_output_event(SetVisualState(VisualState.IDLE))  # connect
        await asyncio.sleep(0.05)  # the initial state has arrived
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert time.monotonic() - started >= earliest
        assert await mic.wait_for_event() == CueFinished("cue-1")
        assert [name for name, _ in clients.last.timeline][-3:] == ["IDLE", "PLAYING", "IDLE"]

    run(lambda: scenario(0.2, esphome.CUE_MIN_SECONDS))  # the floor holds
    run(lambda: scenario(0.8, 0.8))  # then the cue's own PLAYING holds


def test_a_disconnect_during_the_cue_wait_is_unavailable_at_once() -> None:
    async def scenario() -> None:
        mic, clients = make()
        clients.setup = lambda client: setattr(client, "cue_seconds", 5.0)
        await mic.send_output_event(SetVisualState(VisualState.IDLE))  # connect
        client = clients.last
        cue = asyncio.create_task(
            mic.driver.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        )
        await asyncio.sleep(0.1)  # inside the floor, which waits on the player event
        assert not cue.done()
        assert client.on_stop is not None
        started = time.monotonic()
        await client.on_stop(False)
        with pytest.raises(MicrophoneUnavailable, match="transport stopped"):
            await asyncio.wait_for(cue, 1.0)
        assert time.monotonic() - started < 0.1  # a sleeping floor would take about 0.4 s

    run(scenario)


def test_a_reconnect_ignores_old_updates_and_its_first_state(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The old client's updates change nothing, and the new connection's initial state (it
    arrives during the cue wait here) is not the cue's update."""
    monkeypatch.setattr(esphome, "CUE_START_TIMEOUT_SECONDS", 0.5)

    async def scenario() -> None:
        mic, clients = make()
        await mic.send_output_event(SetVisualState(VisualState.IDLE))  # connect
        old = clients.last
        await asyncio.sleep(0.05)
        assert old.on_stop is not None
        await old.on_stop(False)
        with pytest.raises(MicrophoneUnavailable):
            await mic.wait_for_event()
        await mic.close()
        mic.protocol = MicrophoneProtocolState()  # the session recreates its boundary
        clients.setup = lambda client: setattr(client, "cue_announces", False)

        async def old_updates() -> None:
            for _ in range(30):
                await old.player(MediaPlayerState.PLAYING)  # would also block playback
                await old.player(MediaPlayerState.IDLE)
                await asyncio.sleep(0.02)

        noise = asyncio.create_task(old_updates())
        started = time.monotonic()
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))  # reconnects
        assert time.monotonic() - started >= 0.5  # waited out the start bound
        assert await mic.wait_for_event() == CueFinished("cue-1")
        await noise
        assert len(clients.made) == 2

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "cue play_message_end_cue: the media player reported no change" in caplog.text


def test_the_hardware_sequence_cue_then_url_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    """The sequence that failed on voice-pe-03: the accepted cue reports one merged IDLE about
    0.1 s after the call; the URL goes out only after it (and the floor), is fetched, and the
    player reports PLAYING, then IDLE."""

    async def scenario() -> None:
        async with serving() as server:
            mic, clients = await playing(server)
            client = clients.last
            client.cue_merged = True
            client.cue_start_seconds = 0.0
            client.cue_seconds = 0.1
            client.url_states = True
            await asyncio.sleep(0.05)
            called = asyncio.get_running_loop().time()
            await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
            assert await mic.wait_for_event() == CueFinished("cue-1")
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.send_output_event(PlaybackChunk("pb-1", bytes(BYTE_RATE // 10)))
            await mic.send_output_event(PlaybackEnd("pb-1"))
            assert await mic.wait_for_event() == PlaybackFinished("pb-1")
            after = [(name, t - called) for name, t in client.timeline if t >= called]
            assert [name for name, _ in after] == ["IDLE", "url", "PLAYING", "IDLE"]
            cue_idle, url = after[0][1], after[1][1]
            assert cue_idle < url
            assert url >= esphome.CUE_MIN_SECONDS

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "still busy" not in caplog.text and "reported no change" not in caplog.text


def test_a_failed_cue_response_is_logged_and_the_cue_still_finishes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        mic, clients = make()

        def setup(client: FakeClient) -> None:
            client.responding.update({"play_message_end_cue": SupportsResponseType.OPTIONAL})
            client.response = Response(False, "speaker busy")

        clients.setup = setup
        await mic.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert await mic.wait_for_event() == CueFinished("cue-1")

    with caplog.at_level(logging.WARNING):
        run(scenario)
    assert "cue service play_message_end_cue failed: speaker busy" in caplog.text


def test_a_cue_response_that_never_comes_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(esphome, "CUE_RESPONSE_TIMEOUT_SECONDS", 0.05)

    async def scenario() -> None:
        mic, clients = make()

        def setup(client: FakeClient) -> None:
            client.responding.update({"play_message_end_cue": SupportsResponseType.OPTIONAL})
            client.hang.add("play_message_end_cue")

        clients.setup = setup
        with pytest.raises(MicrophoneUnavailable, match="play_message_end_cue failed"):
            await mic.driver.send_output_event(PlayCue("cue-1", CueType.UTTERANCE_ACCEPTED))
        assert clients.last.disconnects == 1

    run(scenario)
