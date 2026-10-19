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
