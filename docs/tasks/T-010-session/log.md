# T-010 log (append-only)

## 2026-10-18: start

- Branch `t-010-session` from origin/main 1a4b342 (PR #11, the T-013 follow-up, is open and
  not a dependency).
- Read design.md §4.1, §6, §9, §10 and the README T-010 row and carry-forwards; wrote plan.md.
- Both "driver follow-up (before T-010)" items are done here (plan carry-forwards 5 and 6).

## 2026-10-18: implementation

- `hub/events.py` (the §6 events as frozen dataclasses, no envelope), `hub/session.py`
  (`SatelliteSession`), tests in `tests/test_session.py` (fake driver that answers like the
  real one and records command overlap, fake STT, short real timeouts).
- Driver: `_fail()` wakes the start and stop-ack waits, which re-check `_unavailable`;
  `_connect` disconnects the client when cancelled. The three new driver tests fail against
  origin/main's driver and pass now.
- Decisions taken from design.md and recorded in plan.md: `wake` is emitted before an accept
  even when only the final matched; `transcript_partial` only for matching partials with a
  non-empty remainder; `satellite` events only on change; API state `idle` = armed without a
  candidate, `listening` = candidate; invariant failures propagate (§9), unlike T-013.
- README: T-013 merged, T-010 in progress, the four T-010/driver carry-forwards marked done.
- `make check`: 405 passed, 1 skipped (run 3 times, no flakes).
