"""T-014: the shared playback HTTP server and ESPHome playback and cue responses (no device:
a fake client whose media player fetches the announced URL over loopback)."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from aioesphomeapi.model import SupportsResponseType
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
    PlayCue,
    SetVisualState,
    VisualState,
)

RATE, WIDTH, CHANNELS = 22050, 2, 1
BYTE_RATE = RATE * WIDTH * CHANNELS
OK_HEAD = b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nConnection: close\r\n\r\n"


@asynccontextmanager
async def serving() -> AsyncIterator[PlaybackHttpServer]:
    server = PlaybackHttpServer("127.0.0.1", 0)
    await server.start()
    try:
        yield server
    finally:
        await server.close()


async def get(server: PlaybackHttpServer, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(request)
    try:
        return await asyncio.wait_for(reader.read(), 2.0)
    finally:
        writer.close()


async def get_wav(server: PlaybackHttpServer, playback_id: str) -> bytes:
    return await get(server, f"GET /{playback_id}.wav HTTP/1.1\r\nHost: x\r\n\r\n".encode())


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
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS)
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
            stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS)
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
        stream = PlaybackStream("pb-1", RATE, WIDTH, CHANNELS)
        stream.write(bytes(BYTE_RATE))
        with pytest.raises(playback_http.StreamFull, match="exceeds 1 s"):
            stream.write(b"\0\0")

    run(scenario)


def test_remaining_seconds_counts_from_the_first_chunk_sent() -> None:
    async def scenario() -> None:
        now = [100.0]
        stream = PlaybackStream("pb-1", RATE, WIDTH, CHANNELS, clock=lambda: now[0])
        stream.write(bytes(2 * BYTE_RATE))
        assert stream.audio_seconds == 2.0 and stream.remaining_seconds() == 0.0  # not sent
        stream.first_audio_at = 100.0
        now[0] = 100.5
        assert stream.remaining_seconds() == 1.5
        now[0] = 103.0
        assert stream.remaining_seconds() == -1.0

    run(scenario)


def test_close_ends_an_open_get_and_unblocks_its_done() -> None:
    async def scenario() -> None:
        server = PlaybackHttpServer("127.0.0.1", 0)
        await server.start()
        stream = server.open_stream("pb-1", RATE, WIDTH, CHANNELS)
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


def test_the_url_uses_the_local_ip_on_the_route_to_the_connected_address() -> None:
    async def scenario() -> None:
        async with serving() as server:
            asked: list[str] = []

            def local_ip(device_ip: str) -> str:
                asked.append(device_ip)
                return "127.0.0.1"

            mic, clients = make(playback_server=server, local_ip=local_ip)
            clients.setup = lambda client: setattr(client, "connected_address", "10.0.0.9")
            await mic.send_output_event(PlaybackBegin("pb-1", RATE, WIDTH, CHANNELS))
            await mic.close()
            mic, clients = make(playback_server=server, local_ip=local_ip)
            clients.setup = lambda client: setattr(client, "connected_address", None)
            await mic.send_output_event(PlaybackBegin("pb-2", RATE, WIDTH, CHANNELS))
            await mic.close()
            assert asked == ["10.0.0.9", "10.0.0.5"]  # else the configured address

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


def test_a_cue_without_supports_response_stays_fire_and_forget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        mic, clients = make()
        await mic.send_output_event(PlayCue("cue-1", CueType.TIMEOUT))
        assert await mic.wait_for_event() == CueFinished("cue-1")
        assert clients.last.call_kwargs == [{}]

    with caplog.at_level(logging.INFO):
        run(scenario)
    assert "cue services without supports_response" in caplog.text
    names = [n for n in ALL_SERVICES if n.startswith("play_")]
    assert str(names) in caplog.text


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
