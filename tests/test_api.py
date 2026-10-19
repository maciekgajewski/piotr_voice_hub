"""T-011: the WebSocket API, with a real websockets client on 127.0.0.1."""

import asyncio
import json
import logging
import socket
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from piotr_voice_hub.hub import api as api_module
from piotr_voice_hub.hub.api import ApiServer, event_message, format_ts
from piotr_voice_hub.hub.events import (
    HubEvent,
    SatelliteState,
    SatelliteStatus,
    Speaker,
    TranscriptFinal,
    TranscriptPartial,
    Wake,
    WakeCancelled,
)

TIMEOUT = 5.0
T0 = datetime(2026, 10, 18, 16, 40, 12, 120456, tzinfo=UTC)
TS0 = "2026-10-18T16:40:12.120Z"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        moment = self.now
        self.now += timedelta(milliseconds=1)
        return moment


def status(satellite: str, connected: bool, state: SatelliteState) -> SatelliteStatus:
    return SatelliteStatus(satellite, "kitchen", connected, state)


def final(utterance_id: str, text: str = "jaka pogoda") -> TranscriptFinal:
    return TranscriptFinal(
        "voice-pe-02", utterance_id, text, f"Ryszardzie, {text}", False, None, 2380
    )


def wakes(n: int) -> list[HubEvent]:
    return [Wake("voice-pe-02", f"utterance-{i}") for i in range(n)]


@asynccontextmanager
async def running_server(clock: Callable[[], datetime] | None = None) -> AsyncIterator[ApiServer]:
    server = ApiServer("127.0.0.1", 0, clock or Clock())
    await server.start()
    try:
        yield server
    finally:
        await server.close()
        await assert_no_tasks_left()


async def assert_no_tasks_left() -> None:
    await asyncio.sleep(0.01)  # let finished connection tasks be reaped
    left = asyncio.all_tasks() - {asyncio.current_task()}
    assert not left, left


def url(server: ApiServer, path: str = "/ws") -> str:
    return f"ws://127.0.0.1:{server.port}{path}"


async def receive(client: ClientConnection, n: int) -> list[dict[str, Any]]:
    async with asyncio.timeout(TIMEOUT):
        return [json.loads(await client.recv()) for _ in range(n)]


async def until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not condition():
            await asyncio.sleep(0.001)


async def stuck_client(server: ApiServer) -> socket.socket:
    """A WebSocket client that completes the handshake and then never reads, with a small fixed
    receive buffer (no autotuning), so the server's writes soon block."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024)
    sock.setblocking(False)
    await loop.sock_connect(sock, ("127.0.0.1", server.port))
    request = (
        "GET /ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    await loop.sock_sendall(sock, request.encode())
    response = b""
    async with asyncio.timeout(TIMEOUT):
        while b"\r\n\r\n" not in response:
            response += await loop.sock_recv(sock, 1)  # byte by byte: nothing past the headers
    assert response.startswith(b"HTTP/1.1 101"), response
    return sock


async def until_closed_by_server(sock: socket.socket) -> None:
    """Read (and discard) until the server closes or resets the connection."""
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(TIMEOUT):
        try:
            while await loop.sock_recv(sock, 1 << 20):
                pass
        except ConnectionResetError:
            pass
    sock.close()


def utterance_ids(messages: list[dict[str, Any]]) -> list[str]:
    return [m["data"]["utterance_id"] for m in messages]


# Serialization


def test_envelope_of_every_event_type() -> None:
    sat = "voice-pe-02"
    assert event_message(SatelliteStatus(sat, "living_room", True, SatelliteState.IDLE), TS0) == {
        "type": "satellite",
        "satellite": sat,
        "ts": TS0,
        "data": {"area": "living_room", "connected": True, "state": "idle"},
    }
    assert event_message(Wake(sat, "u1"), TS0)["data"] == {"utterance_id": "u1"}
    assert event_message(Wake(sat, "u1"), TS0)["type"] == "wake"
    cancelled = event_message(WakeCancelled(sat, "u1"), TS0)
    assert (cancelled["type"], cancelled["data"]) == ("wake_cancelled", {"utterance_id": "u1"})
    partial = event_message(TranscriptPartial(sat, "u1", "jaka"), TS0)
    assert (partial["type"], partial["data"]) == (
        "transcript_partial",
        {"utterance_id": "u1", "text": "jaka"},
    )
    assert event_message(final("u1"), TS0) == {
        "type": "transcript_final",
        "satellite": sat,
        "ts": TS0,
        "data": {
            "utterance_id": "u1",
            "text": "jaka pogoda",
            "raw_text": "Ryszardzie, jaka pogoda",
            "follow_up": False,
            "speaker": None,
            "audio_ms": 2380,
        },
    }
    with_speaker = TranscriptFinal(
        sat, "u2", "a", "Ryszardzie, a", False, Speaker("Maciek", 0.81), 1
    )
    assert event_message(with_speaker, TS0)["data"]["speaker"] == {
        "name": "Maciek",
        "confidence": 0.81,
    }


def test_state_values_follow_the_design() -> None:
    states = {s: event_message(status("s", True, s), TS0)["data"]["state"] for s in SatelliteState}
    assert states == {
        SatelliteState.IDLE: "idle",
        SatelliteState.LISTENING: "listening",
        SatelliteState.PROCESSING: "processing",
        SatelliteState.ERROR: "error",
    }


def test_ts_is_utc_with_milliseconds() -> None:
    assert format_ts(T0) == TS0
    warsaw = T0.astimezone(timezone(timedelta(hours=2)))
    assert format_ts(warsaw) == TS0
    with pytest.raises(AssertionError):
        format_ts(datetime(2026, 10, 18))


# Connect and delivery


def test_connect_gets_the_satellite_snapshot_then_events_in_order() -> None:
    asyncio.run(_test_connect_gets_the_satellite_snapshot_then_events_in_order())


async def _test_connect_gets_the_satellite_snapshot_then_events_in_order() -> None:
    async with running_server() as server:
        server.publish(status("voice-pe-02", False, SatelliteState.ERROR))
        server.publish(status("voice-pe-03", False, SatelliteState.ERROR))
        server.publish(status("voice-pe-02", True, SatelliteState.IDLE))  # replaces the first
        server.publish(Wake("voice-pe-02", "before-connect"))  # not replayed
        async with connect(url(server)) as client:
            snapshot = await receive(client, 2)
            assert [(m["satellite"], m["data"]["state"]) for m in snapshot] == [
                ("voice-pe-02", "idle"),
                ("voice-pe-03", "error"),
            ]
            assert snapshot[0]["ts"] == "2026-10-18T16:40:12.122Z"  # its original ts
            events: list[HubEvent] = [
                Wake("voice-pe-02", "u1"),
                status("voice-pe-02", True, SatelliteState.LISTENING),
                TranscriptPartial("voice-pe-02", "u1", "jaka"),
                final("u1", "jaka będzie pogoda"),
            ]
            for event in events:
                server.publish(event)
            messages = await receive(client, 4)
            assert [m["type"] for m in messages] == [
                "wake",
                "satellite",
                "transcript_partial",
                "transcript_final",
            ]
            assert messages[3]["data"]["text"] == "jaka będzie pogoda"
            assert [m["ts"] for m in messages] == [
                f"2026-10-18T16:40:12.{ms}Z" for ms in ("124", "125", "126", "127")
            ]


def test_raw_json_is_utf8_not_escaped() -> None:
    asyncio.run(_test_raw_json_is_utf8_not_escaped())


async def _test_raw_json_is_utf8_not_escaped() -> None:
    async with running_server() as server, connect(url(server)) as client:
        await until(lambda: server.client_count == 1)
        server.publish(final("u1", "zażółć"))
        async with asyncio.timeout(TIMEOUT):
            text = await client.recv()
        assert isinstance(text, str) and "zażółć" in text


def test_every_client_gets_the_same_ordered_stream() -> None:
    asyncio.run(_test_every_client_gets_the_same_ordered_stream())


async def _test_every_client_gets_the_same_ordered_stream() -> None:
    async with (
        running_server() as server,
        connect(url(server)) as a,
        connect(url(server)) as b,
    ):
        await until(lambda: server.client_count == 2)
        events = wakes(200)
        for i, event in enumerate(events):
            server.publish(event)
            if i % 10 == 0:
                await asyncio.sleep(0)
        expected = [f"utterance-{i}" for i in range(200)]
        assert utterance_ids(await receive(a, 200)) == expected
        assert utterance_ids(await receive(b, 200)) == expected


def test_client_disconnect_is_removed_and_others_keep_receiving() -> None:
    asyncio.run(_test_client_disconnect_is_removed_and_others_keep_receiving())


async def _test_client_disconnect_is_removed_and_others_keep_receiving() -> None:
    async with running_server() as server, connect(url(server)) as stays:
        leaves = await connect(url(server))
        await until(lambda: server.client_count == 2)
        server.publish(Wake("voice-pe-02", "u1"))
        await receive(leaves, 1)
        await leaves.close()
        await until(lambda: server.client_count == 1)
        server.publish(Wake("voice-pe-02", "u2"))
        assert utterance_ids(await receive(stays, 2)) == ["u1", "u2"]


def test_client_messages_are_ignored(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=api_module.__name__)
    asyncio.run(_test_client_messages_are_ignored())
    assert caplog.text.count("ignoring client messages (commands are P1)") == 1


async def _test_client_messages_are_ignored() -> None:
    async with running_server() as server, connect(url(server)) as client:
        await until(lambda: server.client_count == 1)
        for _ in range(3):
            await client.send('{"type":"speak","id":"c1","satellite":"voice-pe-02","data":{}}')
        await client.send(b"\x00binary")
        server.publish(Wake("voice-pe-02", "u1"))
        assert utterance_ids(await receive(client, 1)) == ["u1"]
        assert server.client_count == 1


# Slow clients


def test_a_burst_over_the_queue_disconnects_the_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(api_module, "CLIENT_QUEUE_EVENTS", 4)
    asyncio.run(_test_a_burst_over_the_queue_disconnects_the_client())
    assert caplog.text.count("too slow (4 events queued); disconnecting") == 1


async def _test_a_burst_over_the_queue_disconnects_the_client() -> None:
    async with running_server() as server:
        async with connect(url(server)) as client:
            await until(lambda: server.client_count == 1)
            for event in wakes(5):  # one synchronous burst: the sender cannot run in between
                server.publish(event)
            assert server.client_count == 0
            server.publish(Wake("voice-pe-02", "after"))  # not queued for the dropped client
            received: list[str] = []
            with pytest.raises(ConnectionClosed) as closed:
                async with asyncio.timeout(TIMEOUT):
                    while True:
                        received.append(json.loads(await client.recv())["data"]["utterance_id"])
            assert closed.value.rcvd is not None
            assert (closed.value.rcvd.code, closed.value.rcvd.reason) == (1008, "too slow")
            assert received == [f"utterance-{i}" for i in range(len(received))]  # a prefix
        async with connect(url(server)) as again:  # reconnecting works
            await until(lambda: server.client_count == 1)
            server.publish(Wake("voice-pe-02", "u1"))
            assert utterance_ids(await receive(again, 1)) == ["u1"]


def test_a_client_that_stops_reading_is_dropped_and_a_reading_one_gets_everything(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(api_module, "CLIENT_QUEUE_EVENTS", 4)
    monkeypatch.setattr(api_module, "CLOSE_TIMEOUT_SECONDS", 0.2)
    asyncio.run(_test_a_client_that_stops_reading_is_dropped_and_a_reading_one_gets_everything())
    assert caplog.text.count("too slow") == 1


async def _test_a_client_that_stops_reading_is_dropped_and_a_reading_one_gets_everything() -> None:
    big = "a" * 60_000
    async with (
        running_server() as server,
        connect(url(server)) as reader,
    ):
        stuck = await stuck_client(server)
        await until(lambda: server.client_count == 2)
        received: list[str] = []

        async def read() -> None:
            while True:
                received.append(json.loads(await reader.recv())["data"]["utterance_id"])

        reading = asyncio.create_task(read())
        published = 0
        async with asyncio.timeout(TIMEOUT * 2):
            while server.client_count == 2:
                server.publish(TranscriptPartial("voice-pe-02", f"u{published}", big))
                published += 1
                await asyncio.sleep(0)
        await until(lambda: len(received) == published)
        reading.cancel()
        assert received == [f"u{i}" for i in range(published)]
        await until_closed_by_server(stuck)  # closed with 1008 or aborted, within a bound


# Health and routing


def test_healthz_returns_the_satellite_states() -> None:
    asyncio.run(_test_healthz_returns_the_satellite_states())


def _get(address: str) -> tuple[int, str, str]:
    with urllib.request.urlopen(address, timeout=TIMEOUT) as response:
        return response.status, response.headers["Content-Type"], response.read().decode()


async def _test_healthz_returns_the_satellite_states() -> None:
    async with running_server() as server:
        base = f"http://127.0.0.1:{server.port}"
        assert await asyncio.to_thread(_get, base + "/healthz") == (
            200,
            "application/json",
            '{"satellites":{}}\n',
        )
        server.publish(SatelliteStatus("voice-pe-02", "living_room", True, SatelliteState.IDLE))
        server.publish(status("voice-pe-03", False, SatelliteState.ERROR))
        server.publish(
            SatelliteStatus("voice-pe-02", "living_room", True, SatelliteState.LISTENING)
        )
        code, content_type, body = await asyncio.to_thread(_get, base + "/healthz")
        assert (code, content_type) == (200, "application/json")
        assert json.loads(body) == {
            "satellites": {
                "voice-pe-02": {"area": "living_room", "connected": True, "state": "listening"},
                "voice-pe-03": {"area": "kitchen", "connected": False, "state": "error"},
            }
        }
        with pytest.raises(urllib.error.HTTPError) as error:
            await asyncio.to_thread(_get, base + "/other")
        assert error.value.code == 404
        error.value.close()
        with pytest.raises(InvalidStatus):
            await connect(url(server, "/"))


# Shutdown


def test_close_sends_going_away_and_releases_the_port() -> None:
    asyncio.run(_test_close_sends_going_away_and_releases_the_port())


async def _test_close_sends_going_away_and_releases_the_port() -> None:
    server = ApiServer("127.0.0.1", 0, Clock())
    await server.start()
    port = server.port
    async with connect(url(server)) as a, connect(url(server)) as b:
        await until(lambda: server.client_count == 2)
        async with asyncio.timeout(TIMEOUT):
            await server.close()
        assert server.client_count == 0
        for client in (a, b):
            with pytest.raises(ConnectionClosed) as closed:
                await client.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1001
    with pytest.raises(OSError):
        await connect(f"ws://127.0.0.1:{port}/ws", open_timeout=TIMEOUT)
    await server.close()  # idempotent
    server.publish(Wake("voice-pe-02", "u1"))  # harmless after close
    await assert_no_tasks_left()


def test_close_is_bounded_with_a_stuck_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(api_module, "SHUTDOWN_GRACE_SECONDS", 0.3)
    asyncio.run(_test_close_is_bounded_with_a_stuck_client())
    assert "1 connections did not close in time; aborting" in caplog.text


async def _test_close_is_bounded_with_a_stuck_client() -> None:
    server = ApiServer("127.0.0.1", 0, Clock())
    await server.start()
    stuck = await stuck_client(server)
    await until(lambda: server.client_count == 1)
    for i in range(100):  # about 6 MB: more than the socket buffers hold, fewer than the queue
        server.publish(TranscriptPartial("voice-pe-02", f"u{i}", "a" * 60_000))
        await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await server.close()
    assert loop.time() - started < 0.3 + api_module.SHUTDOWN_ABORT_SECONDS
    assert server.client_count == 0
    await until_closed_by_server(stuck)
    await assert_no_tasks_left()


async def silent_client(server: ApiServer) -> socket.socket:
    """A TCP connection that never sends its HTTP request."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", server.port))
    return sock


def test_a_handshake_that_never_completes_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_module, "OPEN_TIMEOUT_SECONDS", 0.2)
    asyncio.run(_test_a_handshake_that_never_completes_is_dropped())


async def _test_a_handshake_that_never_completes_is_dropped() -> None:
    async with running_server() as server:
        sock = await silent_client(server)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await until_closed_by_server(sock)
        assert 0.15 < loop.time() - started < 0.2 + 1.0


def test_close_is_bounded_with_a_connection_that_never_sends_its_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # OPEN_TIMEOUT_SECONDS (3 s) is longer than the grace: the abort path must end it.
    monkeypatch.setattr(api_module, "SHUTDOWN_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(api_module, "SHUTDOWN_ABORT_SECONDS", 0.5)
    asyncio.run(_test_close_is_bounded_with_a_connection_that_never_sends_its_request())
    assert "1 connections did not close in time; aborting" in caplog.text


async def _test_close_is_bounded_with_a_connection_that_never_sends_its_request() -> None:
    server = ApiServer("127.0.0.1", 0, Clock())
    await server.start()
    sock = await silent_client(server)
    async with connect(url(server)) as client:
        await until(lambda: server.client_count == 1)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await server.close()
        assert loop.time() - started < 0.2 + 0.5
        with pytest.raises(ConnectionClosed) as closed:
            await client.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1001
    await until_closed_by_server(sock)
    await assert_no_tasks_left()


def test_a_send_blocked_too_long_disconnects_the_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(api_module, "SEND_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(api_module, "CLOSE_TIMEOUT_SECONDS", 0.2)
    asyncio.run(_test_a_send_blocked_too_long_disconnects_the_client())
    assert caplog.text.count("too slow (a send blocked for 0.2 s); disconnecting") == 1


async def _test_a_send_blocked_too_long_disconnects_the_client() -> None:
    async with running_server() as server:
        stuck = await stuck_client(server)
        await until(lambda: server.client_count == 1)
        for i in range(100):  # about 6 MB, fewer events than the queue holds
            server.publish(TranscriptPartial("voice-pe-02", f"u{i}", "a" * 60_000))
            await asyncio.sleep(0)
        await until(lambda: server.client_count == 0)
        await until_closed_by_server(stuck)


# Client cap


def test_the_17th_client_gets_503_until_a_slot_frees(caplog: pytest.LogCaptureFixture) -> None:
    asyncio.run(_test_the_17th_client_gets_503_until_a_slot_frees())
    assert caplog.text.count("clients connected") == 1


async def _test_the_17th_client_gets_503_until_a_slot_frees() -> None:
    assert api_module.MAX_CLIENTS == 16
    async with running_server() as server:
        clients = [await connect(url(server)) for _ in range(16)]
        await until(lambda: server.client_count == 16)
        with pytest.raises(InvalidStatus) as refused:
            await connect(url(server))
        assert refused.value.response.status_code == 503
        base = f"http://127.0.0.1:{server.port}"
        assert (await asyncio.to_thread(_get, base + "/healthz"))[0] == 200  # not capped
        await clients.pop().close()
        await until(lambda: len(server._admitted) == 15)
        clients.append(await connect(url(server)))
        await until(lambda: server.client_count == 16)
        server.publish(Wake("voice-pe-02", "u1"))
        for client in clients:
            assert utterance_ids(await receive(client, 1)) == ["u1"]
        for client in clients:
            await client.close()


def test_a_slow_client_is_closed_within_the_bound_even_if_it_never_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_module, "CLIENT_QUEUE_EVENTS", 4)
    monkeypatch.setattr(api_module, "CLOSE_TIMEOUT_SECONDS", 0.2)
    asyncio.run(_test_a_slow_client_is_closed_within_the_bound_even_if_it_never_reads())


async def _test_a_slow_client_is_closed_within_the_bound_even_if_it_never_reads() -> None:
    async with running_server() as server:
        stuck = await stuck_client(server)
        await until(lambda: server.client_count == 1)
        (client,) = server._clients
        published = 0
        async with asyncio.timeout(TIMEOUT):
            while server.client_count == 1:
                server.publish(TranscriptPartial("voice-pe-02", f"u{published}", "a" * 60_000))
                published += 1
                await asyncio.sleep(0)
        async with asyncio.timeout(0.2 + 1.0):  # the client still does not read
            await client.connection.wait_closed()
        stuck.close()
