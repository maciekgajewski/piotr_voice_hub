# T-020: log

## 2026-10-30

- Started from origin/main e60e03a (T-019 merged as PR #28) in a new worktree
  `~/src/piotr_voice_hub-t020`, branch `t-020-follow-up-listening`.
- Read AGENTS.md, design.md §4.1 step 7 and §6, microphone-protocol.md (visual table,
  follow-up timeout row), the tasks README (T-020 row and note: no ready cue until the P2
  firmware, Maciej 2026-10-19) and the T-019 plan. Interpretations in plan.md: the window after
  any `listen_after` speak that played to its end (announcements too); none after `stop`, no
  audio or a failure; `listening` and busy during the window; the timeout cue on expiry (step 7)
  since the decision drops only the ready cue; a segment with no request text ends the window.
- Implemented in the session: `_follow_up_next` set by the speak, consumed by `_arm`, a
  deadline read in `_listen_until_accepted`, `_end_follow_up` for the timeout. No new task;
  `run()` stays the only sender. `follow_up_timeout` event in the API.
- First run of the new busy test timed out: the "follow-up" `Ehm` has letters, so it was a
  request, not noise; the test uses `...` now. One expectation mixed up the third
  `PlaybackFinished` field (`listening_for_follow_up`, not "played").
- ruff: one long line in `_arm`, one in a test; pyright: the tool's reply data typed as
  `dict[str, Any]`. `make check`: 880 passed, 8 skipped.
- Folded in the PR #28 re-review: N1 T-019 plan status done; N2 design §6, clients must still
  handle `busy` after `idle`. Tasks README: T-019 merged (PR #28), T-020 in review.
