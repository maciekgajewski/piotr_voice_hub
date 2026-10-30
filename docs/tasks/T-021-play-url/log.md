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
