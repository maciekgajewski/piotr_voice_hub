# T-021: log

## 2026-10-30

- Started from origin/main fa4316c (T-020 merged as PR #29) in a new worktree
  `~/src/piotr_voice_hub-t021`, branch `t-021-play-url`.
- Read AGENTS.md, design.md §4.1 steps 5 to 8 and §6, microphone-protocol.md, the tasks
  README (T-021 row, note "P1 → T-021", T-014 decision Q3) and the T-019/T-020 plans.
  Interpretations in plan.md: busy policy and announcements as `speak`, no volume, `http`/
  `https` with a host, a new `PlaybackUrl` driver command with its end from the player state
  (the cue rule, bounded at 10 min), a never-played URL as a WARNING plus one
  `playback_finished` (no reason field in the protocol).
- Implemented the driver command, the session `play` (shared `_take_output` with `speak`),
  command validation and `tools/speak_test.py --play`.
- Driver log test first found nothing: the driver logger
  (`piotr_voice_hub.satellites.esphome[<id>]`) is not a child of the package logger, so the
  test captures at the root.
- ruff: long docstring lines reflowed; pyright: the fake session's call list typed
  `tuple[Any, ...]`. `make check`: 946 passed, 8 skipped. The new tests run 5 times: no
  failures.
- Docs: design.md §4.1 step 6 (reachability, end, failures), steps 7 and 8, §6 rows and busy
  policy; microphone-protocol.md `PlaybackUrl` command, state row, sequencing and timeouts.
  Tasks README: T-020 merged (PR #29), T-021 in review, notes updated; T-020 plan status done.

- PR #30 review (CHANGES NEEDED; all eight interpretations agreed), fixed on the same branch:
  - B: a play could outlive the hub. `close()` now sends the media player `STOP` for a current
    URL, and every connect stops a busy player before anything uses the device (bounded 3 s
    for the initial state and 3 s for idle, WARNINGs). The driver's `_ready` flag (set after
    the check) replaces "client published" as the connected test, so a command racing the
    reader's connect waits for the check. Other paths checked: a lost connection, a driver
    failure and the session's 720 s give-up all recover through `close()` and a reconnect; a
    hub shutdown calls `close()`, a restart reconnects. A dropped API consumer ends nothing.
  - S1: the INFO end line carries the seconds since the call; docs and plan describe both
    failure paths; the checklist records which one each case takes.
  - S2: checklist on a LAN http server on the laptop, one https step, new cases (LAN 404,
    refused port, a 0.3 s beep, an endless radio with `--stop-after`, a hub restart mid-radio).
  - N4: docs reworded (the protocol has no reason, so the session treats it as played; a
    failed reply is the accepted cue, then silence). N5: `PlaybackUrl.url` is
    `field(repr=False)`; the DEBUG command line shows only the id. N6: the deaf-for-its-length
    sentence in design §4.1 step 6. N7: tests for the 720 s bound, 2048/2049, the wait after
    the STOP at the cap, and no ERROR on a disconnect mid-URL. The fourth mutation (dropping
    `except MicrophoneUnavailable` in `_drain`) could not be killed: the clause was dead code,
    since every failure goes through `_fail()`, which releases the playback and so cancels the
    drain before any `_wait_player` in it can raise. Removed it; the no-ERROR test pins the
    behaviour. Each mutation (N7, B, S1, N5) was applied and reverted from a /tmp backup, and
    each one failed a test.
- Checklist step 11b (manager's follow-up on PR #30): a `docker kill` + `docker start` while
  the radio plays, so `close()` never runs and the connect check alone must stop the radio
  (WARNING "the media player is PLAYING at connect …", INFO "stopped the media player at
  connect"). Records the path, the seconds from `docker start` to silence and the two lines'
  timestamps (`docker logs -t`). Docs only; step 11 unchanged.
