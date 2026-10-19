# T-011 log (append-only)

## 2026-10-19: start

- Branch `t-011-api` from origin/main d3fb334 (PR #12, T-010, squash-merged).
- Read design.md §5, §6, §9, the README T-011 row and the old `ai_server/websocket_server.py`
  (read-only: aiohttp, admission control, per-session queues, close codes). Wrote plan.md.
- Library: nothing WebSocket-capable in uv.lock. Added `websockets>=15,<16` (15.0.1, no
  dependencies of its own) rather than aiohttp (§5's note; it would add aiohttp plus six
  packages). design.md §5 updated.
- No design question changes the implementation materially; the decisions (snapshot of
  satellite states on connect, slow client disconnected not dropped, client messages discarded
  in P0, `/healthz`) are in plan.md.

## 2026-10-19: implementation

- `hub/api.py` (`ApiServer`: `start`, sync `publish`, `close`; `event_message`, `format_ts`),
  `tests/test_api.py` (real `websockets` client on 127.0.0.1, port 0).
- The "stops reading" tests use a raw-socket client (handshake, then never read, 16 KiB
  receive buffer). A `websockets` client with `max_queue=1` did not apply backpressure here:
  the server wrote about 650 MB to it before blocking, so it could not stand in for a stuck
  consumer.
- Checked that the slow-client tests fail when `_drop_slow` does not cancel the sender.
- PR #12 re-review nits: `_running` flag and the `shutdown()` assert, the
  `AssertionError`-on-purpose comment, design.md §6 "while the hub runs" (on the
  `wake_cancelled` row), README T-012 wording. Tests for the assert and for an
  `AssertionError` during `shutdown()`.
- box3-office host fixed in `config/hub.example.yaml` and design.md §7
  (`piotr-box3-01-cbfaA8.local`); README carry-forward records it as done, plus a T-012 item for
  wiring the API.

## 2026-10-19: PR #13 review 5568211407

- Confirmed by the review: snapshot on connect, `/healthz`, client messages discarded in P0,
  `websockets.server` logger level left to T-012.
- **B1:** `MAX_CLIENTS = 16`. `_route` returns HTTP 503 for the 17th `/ws` request, before the
  handshake. A slot is held from admission until `connection.wait_closed()` returns (one watcher
  task per connection), so handshakes in flight count and a failed handshake frees its slot.
  Test: 16 connected, the 17th gets 503, `/healthz` still answers, a slot frees after a
  disconnect, and all 16 then receive events.
- **B2:** `open_timeout` 3 s. `close()` waits 4 s for the 1001 closes, then aborts every
  connection in `server.handlers` (websockets' `close()` skips handshaking ones and
  `server.connections` lists only open ones), then waits at most 1 s more: 5 s in total.
  Tests: a TCP client that never sends its request is dropped at `open_timeout`; with such a
  client connected `close()` takes the abort path, finishes within grace + abort, and leaves
  no tasks. Swapping `server.handlers` for `server.connections` makes that test fail.
- **Also bounded: a blocked send.** Reading websockets 15: its keepalive ping, sent while the
  connection is open, waits for the same full socket buffer, so it does not bound a stuck
  `send()`. Added `SEND_TIMEOUT_SECONDS = 10`: a send blocked that long drops the client as too
  slow (test with a client that never reads and fewer events than the queue).
- **N4:** the 2 s bound on the 1008 close is not redundant. Without it, a slow client that never
  reads kept its connection open 20.0 s after the drop (probe on piotr; closed only when the
  keepalive fired), with it 2.0 s. websockets' `close_timeout` only covers waiting for the
  peer's close frame. The comment says so and a new test fails without the bound.
- **N1, N6:** design.md §6 and plan.md: ordering is per connection (a mid-utterance joiner may
  see partials or the final without the wake), the 16-client cap, the slow-client rule, and
  `/healthz` is liveness (200 even with every satellite offline).
- **N2** comment on `send()`'s bounds; **N3** `assert_never` in `event_message`; **N5** one
  `_statuses` dict of (event, message).
- **N7:** the PR #13 CI annotation was a GitHub notice, not ours: "The ubuntu-latest label will
  migrate to Ubuntu 26 beginning October 19, 2026" (actions/runner-images#14748). Pinned
  `runs-on: ubuntu-24.04` so the CI image does not change under us.
