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

## 2026-10-18: PR #11 review nits folded in (review 5564925077)

PR #11 (T-013 follow-up) was squash-merged as c857a02; rebased onto it. Its three nits are
fixed here as an extra commit:

- 1: a cancellation (Ctrl-C) that lands while recovery waits for the partial-reader task was
  swallowed together with the task's own `CancelledError`. `wake_test.py` (`_abandon`),
  `stt/whisper.py` (`close()`) and the T-010 session (`_abandon_segment`, same pattern) now
  wait with `asyncio.wait({task})`, which raises neither the task's error nor its cancellation,
  so the caller's cancellation propagates at once. The task's error is then logged (T-013
  runner: WARNING; STT: DEBUG, it already went to `on_fatal`); in the session a
  `MicrophoneUnavailable` is logged and any other error is re-raised (fail loudly).
  `close()` still gives up after 30 s with `TimeoutError`.
- 2: the T-013 test helper `_run_then_cancel` and the cancel test assert that no commands
  overlapped.
- 3: a comment in `SatelliteRunner.shutdown`: a Ctrl-C during an in-flight `StartListening`
  only disconnects (the listen id is recorded only once the arm returns).
- Tests: cancel during recovery exits at once (runner and session, with a partial reader that
  takes 0.2 s to unwind; both fail against the old code), partial-reader errors are logged on
  abandon (runner, session) and a non-`MicrophoneUnavailable` one propagates (session), and
  cancelling the caller of STT `close()` propagates.

## 2026-10-18: PR #12 review 5564992469 (CHANGES NEEDED)

- B1 (blocker): cancelling `run()` while a final was running leaked the partial task:
  `_segment` was cleared before `_finish_segment`, so `run()`'s `finally` had nothing to
  abandon, and the task later sent `SetVisualState(LISTENING)` and emitted `wake`. The same
  leak was in `_abandon_segment`, which cleared `_segment` and awaited `stt.close()` before
  cancelling the task. Now the session owns the segment until it is fully finished or
  abandoned: `_finish_segment` clears `_segment` only after the STT session is closed and the
  partial task has ended; `_abandon_segment` cancels the task first, closes the STT session
  in `try`, and waits for the task and clears `_segment` in `finally`. Tests cancel `run()`
  during a final and during an abandon (slow STT close), each with a partial that would
  arrive 0.1 s later, and assert that no command or event follows the cancellation and no
  task is left. The T-013 runner had the same pattern (`_read_segments`/`_abandon`); fixed the
  same way, with a test.
- (1) recovery emits `wake_cancelled` for a `wake` without `transcript_final` (disconnect
  after the candidate, or a failure during the accept). Tests for both.
- (2) design.md §6: `wake` once per utterance (first matching partial, else just before
  `transcript_final`), `wake_cancelled` also on failure, `transcript_partial` only for matching
  partials with the post-phrase text, `satellite` on change, and the state mapping.
- (3) `SatelliteSession.shutdown()`: clean stop on exit (StopListening if armed, idle visual,
  3 s bound, then close; offline: close only; no events). Tests: armed, processing, offline, a
  hanging driver. README: T-012 carry-forward (main calls it; also quiet aioesphomeapi's own
  per-connect WARNING).
- (4) the driver logs a failed connect at INFO; the session's line is the one WARNING per
  attempt.
- (5), (6) plan.md: a partial-task crash is noticed at the next end of speech; the error state
  is shown before the first connect.
