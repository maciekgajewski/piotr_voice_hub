"""WebSocket API, P0 events only (design.md §6, §9; T-011).

One unauthenticated server: `ws://<host>:<port>/ws` broadcasts every hub event to every client in
the §6 envelope, `GET /healthz` (liveness) returns the satellite states. `publish` is the
sessions' `emit` callback. At most MAX_CLIENTS clients. Each client has a bounded queue and a
bounded send; a client that falls behind is disconnected, never fed a gapped stream (design.md
§9). Every wait is bounded, `close()` included. Commands are P1: client messages are discarded.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, assert_never

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.frames import CloseCode
from websockets.http11 import Request, Response

from piotr_voice_hub.hub.events import (
    HubEvent,
    SatelliteStatus,
    TranscriptFinal,
    TranscriptPartial,
    Wake,
    WakeCancelled,
)

WS_PATH = "/ws"
HEALTH_PATH = "/healthz"
MAX_CLIENTS = 16  # more get HTTP 503 before the handshake
CLIENT_QUEUE_EVENTS = 256  # > 40 s of partials from four satellites at once
SEND_TIMEOUT_SECONDS = 10.0  # one message stuck in a full socket buffer: the client is too slow
OPEN_TIMEOUT_SECONDS = 3.0  # a connection that does not finish its handshake is dropped
CLOSE_TIMEOUT_SECONDS = 2.0  # close handshake with one client, then the transport is aborted
SHUTDOWN_GRACE_SECONDS = 4.0  # close(): clients close with 1001 ...
SHUTDOWN_ABORT_SECONDS = 1.0  # ... then every connection is aborted: close() <= 5 s in total
MAX_CLIENT_MESSAGE_BYTES = 64 * 1024
PING_INTERVAL_SECONDS = 20.0
PING_TIMEOUT_SECONDS = 20.0

Clock = Callable[[], datetime]

_logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    """`2026-10-18T16:40:12.120Z`: UTC, milliseconds."""
    assert moment.utcoffset() is not None, "timestamps must be timezone-aware"
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def event_message(event: HubEvent, ts: str) -> dict[str, Any]:
    """The §6 envelope for one hub event."""
    data: dict[str, Any]
    match event:
        case SatelliteStatus():
            kind = "satellite"
            data = {"area": event.area, "connected": event.connected, "state": event.state.value}
        case Wake():
            kind, data = "wake", {"utterance_id": event.utterance_id}
        case WakeCancelled():
            kind, data = "wake_cancelled", {"utterance_id": event.utterance_id}
        case TranscriptPartial():
            kind = "transcript_partial"
            data = {"utterance_id": event.utterance_id, "text": event.text}
        case TranscriptFinal():
            kind = "transcript_final"
            speaker = event.speaker
            data = {
                "utterance_id": event.utterance_id,
                "text": event.text,
                "raw_text": event.raw_text,
                "follow_up": event.follow_up,
                "speaker": None
                if speaker is None
                else {"name": speaker.name, "confidence": speaker.confidence},
                "audio_ms": event.audio_ms,
            }
        case _:
            assert_never(event)
    return {"type": kind, "satellite": event.satellite, "ts": ts, "data": data}


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@dataclass(eq=False)
class _Client:
    connection: ServerConnection
    name: str
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(CLIENT_QUEUE_EVENTS))
    sender: asyncio.Task[None] | None = None
    too_slow: bool = False


class ApiServer:
    """`start()`, then `publish()` from the sessions, then `close()` (T-012 main)."""

    def __init__(self, host: str, port: int, clock: Clock = utc_now) -> None:
        self._host = host
        self._port = port
        self._clock = clock
        self._server: Server | None = None
        self._clients: set[_Client] = set()  # receiving events
        self._admitted: set[ServerConnection] = set()  # passed the cap, until the TCP close
        self._watchers: set[asyncio.Task[None]] = set()  # one per admitted connection
        # The last `satellite` event of each satellite and its message (for new clients).
        self._statuses: dict[str, tuple[SatelliteStatus, str]] = {}
        self._connections = 0

    @property
    def port(self) -> int:
        """The bound port (the configured one, or the OS's choice for port 0)."""
        assert self._server is not None, "not started"
        return next(iter(self._server.sockets)).getsockname()[1]

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        assert self._server is None, "already started"
        self._server = await serve(
            self._serve_client,
            self._host,
            self._port,
            process_request=self._route,
            max_size=MAX_CLIENT_MESSAGE_BYTES,
            ping_interval=PING_INTERVAL_SECONDS,
            ping_timeout=PING_TIMEOUT_SECONDS,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            close_timeout=CLOSE_TIMEOUT_SECONDS,
        )
        _logger.info("API listening on ws://%s:%d%s", self._host, self.port, WS_PATH)

    def publish(self, event: HubEvent) -> None:
        """Queue one event for every client, in call order. Never blocks."""
        text = _dumps(event_message(event, format_ts(self._clock())))
        if isinstance(event, SatelliteStatus):
            self._statuses[event.satellite] = (event, text)
        for client in tuple(self._clients):
            try:
                client.queue.put_nowait(text)
            except asyncio.QueueFull:
                self._drop_slow(client, f"{client.queue.qsize()} events queued")

    async def close(self) -> None:
        """Stop accepting and close every client with 1001. Bounded: after
        SHUTDOWN_GRACE_SECONDS every connection is aborted, handshaking ones included, and the
        wait after that is bounded too, so this takes at most SHUTDOWN_GRACE_SECONDS +
        SHUTDOWN_ABORT_SECONDS."""
        server, self._server = self._server, None
        if server is None:
            return
        server.close(close_connections=True)  # skips connections that are still handshaking
        try:
            async with asyncio.timeout(SHUTDOWN_GRACE_SECONDS):
                await server.wait_closed()
        except TimeoutError:
            # `handlers` holds every accepted TCP connection; `connections` only the open ones.
            stuck = tuple(server.handlers)
            _logger.warning("API: %d connections did not close in time; aborting", len(stuck))
            for connection in stuck:
                connection.transport.abort()
            try:
                async with asyncio.timeout(SHUTDOWN_ABORT_SECONDS):
                    await server.wait_closed()
            except TimeoutError:
                _logger.error("API: connections still open after aborting them")
                return
        _logger.info("API closed")

    # Connections

    def _route(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path == WS_PATH:
            return self._admit(connection)
        if request.path == HEALTH_PATH:
            # Liveness: 200 whenever the process serves, even with every satellite offline.
            satellites = {
                s.satellite: {"area": s.area, "connected": s.connected, "state": s.state.value}
                for s, _ in self._statuses.values()
            }
            response = connection.respond(HTTPStatus.OK, _dumps({"satellites": satellites}) + "\n")
            del response.headers["Content-Type"]
            response.headers["Content-Type"] = "application/json"
            return response
        return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")

    def _admit(self, connection: ServerConnection) -> Response | None:
        """The client cap, before the handshake. A slot is held from here until the TCP
        connection is closed, so handshakes in flight count too and a failed handshake frees
        its slot."""
        if len(self._admitted) >= MAX_CLIENTS:
            _logger.warning(
                "API: refusing %s: %d clients connected", connection.remote_address, MAX_CLIENTS
            )
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "too many clients\n")
        self._admitted.add(connection)
        watcher = asyncio.create_task(connection.wait_closed())
        self._watchers.add(watcher)

        def release(_: asyncio.Task[None]) -> None:
            self._admitted.discard(connection)
            self._watchers.discard(watcher)

        watcher.add_done_callback(release)
        return None

    async def _serve_client(self, connection: ServerConnection) -> None:
        self._connections += 1
        client = _Client(connection, f"client-{self._connections} {connection.remote_address}")
        # Snapshot and registration in one step: no event is missed or sent twice.
        for _, text in self._statuses.values():
            client.queue.put_nowait(text)
        self._clients.add(client)
        _logger.info("API %s connected (%d clients)", client.name, len(self._clients))
        client.sender = asyncio.create_task(self._send_events(client))
        reader = asyncio.create_task(self._discard_messages(client))
        tasks = {client.sender, reader}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self._clients.discard(client)
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks)
            if client.too_slow:
                await self._close_slow(client)
        _logger.info("API %s disconnected (%d clients)", client.name, len(self._clients))
        for task in tasks:
            if not task.cancelled() and (error := task.exception()) is not None:
                raise error

    async def _send_events(self, client: _Client) -> None:
        # send() waits while the socket buffer is full (the peer does not read). That wait is
        # bounded by SEND_TIMEOUT_SECONDS and by the overflow drop (`publish` cancels this
        # task). websockets' keepalive does not bound it: in websockets 15 its ping, sent while
        # the connection is open, waits for the same buffer.
        try:
            while True:
                text = await client.queue.get()
                try:
                    async with asyncio.timeout(SEND_TIMEOUT_SECONDS):
                        await client.connection.send(text)
                except TimeoutError:
                    self._drop_slow(client, f"a send blocked for {SEND_TIMEOUT_SECONDS:g} s")
                    return
        except ConnectionClosed:
            pass

    async def _discard_messages(self, client: _Client) -> None:
        """P0 has no commands. Reading keeps pings and the close handshake flowing."""
        logged = False
        try:
            async for _ in client.connection:
                if not logged:
                    _logger.info("API %s: ignoring client messages (commands are P1)", client.name)
                    logged = True
        except ConnectionClosed:
            pass

    def _drop_slow(self, client: _Client, why: str) -> None:
        if client.too_slow:
            return
        _logger.warning("API %s too slow (%s); disconnecting", client.name, why)
        self._clients.discard(client)
        client.too_slow = True
        assert client.sender is not None
        if client.sender is not asyncio.current_task():
            client.sender.cancel()

    async def _close_slow(self, client: _Client) -> None:
        # Not redundant: websockets' close_timeout covers the wait for the peer's close frame,
        # not writing ours into a socket buffer the peer has stopped draining. Without this
        # bound a slow client's connection stayed open 20 s, until the keepalive (T-011 log).
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await client.connection.close(CloseCode.POLICY_VIOLATION, "too slow")
        except TimeoutError:
            client.connection.transport.abort()
