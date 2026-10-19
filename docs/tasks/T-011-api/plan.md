# T-011: WebSocket API (events)

## Goal

`src/piotr_voice_hub/hub/api.py` (design.md §6, §9): one unauthenticated WebSocket server that
broadcasts the T-010 hub events (`hub/events.py`) to every connected client in the §6 envelope.
T-012 creates it from `api.host`/`api.port`, passes `ApiServer.publish` to every
`SatelliteSession` as `emit`, and closes it on exit.

P0 only (README): events. Commands (`speak`/`play`/`stop`/`release`, `ack`/`error`) are P1.

## Behaviour

- **Library:** `websockets` 15 (asyncio API, no dependencies of its own). Nothing WebSocket-capable
  was in the lockfile; aiohttp (named in §5, from the old project) would add seven packages for
  what we use. design.md §5 is updated.
- **Endpoints:** `ws://<host>:<port>/ws` for events; `GET /healthz` returns
  `{"satellites": {id: {area, connected, state}}}` from the last `satellite` event of each
  satellite; any other path is 404.
- **Envelope:** `{"type", "satellite", "ts", "data"}`. `ts` is the publish time, UTC, ISO 8601
  with milliseconds and `Z` (`2026-10-18T16:40:12.120Z`). `data` is the event's fields without
  `satellite`: `satellite` → `{area, connected, state}`; `wake`/`wake_cancelled` →
  `{utterance_id}`; `transcript_partial` → `{utterance_id, text}`; `transcript_final` →
  `{utterance_id, text, raw_text, follow_up, speaker: {name, confidence} | null, audio_ms}`.
  States are §6's: `idle`, `listening`, `processing`, `error` (`speaking` is P1).
- **Publish:** `publish(event)` is synchronous (the session's `emit`): it serializes once and
  puts the text on every client's bounded queue, so all clients see all events in publish order.
  One sender task per client writes its queue in order.
- **Connect:** a new client first gets the last `satellite` event of each satellite (with its
  original `ts`), queued in the same step that registers it, so nothing is missed or duplicated;
  `satellite` is otherwise sent only on change.
- **Slow client: disconnect, not drop.** When a client's queue (256 events) is full it is
  removed at once and closed with 1008 "too slow" (bounded; the transport is aborted if the
  close handshake does not finish). Dropping events would silently break the per-utterance
  guarantees (every `wake` ends with one `transcript_final` or `wake_cancelled`), so a consumer
  could act on a half-seen utterance; a disconnect is visible and the consumer reconnects and
  gets the snapshot. design.md §9 already says "a slow API client is disconnected rather than
  buffered".
- **Client messages:** P0 has no commands. Incoming messages are read (so pings and close
  frames keep flowing) and discarded, logged once per client at INFO. Max incoming message
  64 KiB.
- **Shutdown:** `close()` stops accepting, closes every client with 1001, and waits a bounded
  time, aborting what is left.
- **Liveness:** websockets' pings (20 s interval, 20 s timeout) drop dead peers.
- No auth (§6); the bind address is the boundary (§9). No connection limit in P0.

## PR #12 re-review nits

1. `SatelliteSession._running` is set for the duration of `run()`; `shutdown()` asserts that it
   is not running. README T-012 carry-forward: "cancel each `run()` task, wait for it, then call
   `shutdown()`".
2. design.md §6: "while the hub runs" on the `wake_cancelled` row (shutdown emits no events, so
   the "every `wake` ends with…" guarantee holds only while the hub runs).
3. `shutdown()` catches `Exception`, which includes `AssertionError`, on purpose: it is
   best effort on the way out, so a protocol assertion (e.g. a command invalid for the state
   `run()` was cancelled in) must not prevent `close()` or the other satellites' shutdown. A
   comment says so; the `_running` assert sits before the `try`, so misuse still raises.

Also: box3-office's real host is `piotr-box3-01-cbfaA8.local` (old config, T-013 live run);
fixed in `config/hub.example.yaml` and design.md §7 here (trivial).

## Tests (`tests/test_api.py`, real `websockets` client on 127.0.0.1, port 0)

- Serialization of every event type and state; `ts` format.
- Connect: snapshot of satellite states, then events in publish order; two clients get the same
  sequence.
- Client disconnect: removed from the server; the other client keeps receiving.
- Slow client: a burst that overflows the queue disconnects it (close 1008) while a reading
  client gets everything; a client that never reads is dropped once the socket buffers fill,
  within a bound.
- Ignored client messages; `/healthz`; 404.
- Server shutdown: clients see 1001, the port is released, close is bounded with a stuck client.
- Session nits: `shutdown()` while `run()` is running raises; an `AssertionError` from a command
  during `shutdown()` is logged and the driver is still closed.

## Manual checklist

Hardware end-to-end only after T-012 (main wiring). Standalone smoke on piotr: start the server
with a fake publisher (snippet in the PR), then `websocat ws://127.0.0.1:8750/ws` and
`curl http://127.0.0.1:8750/healthz`.
