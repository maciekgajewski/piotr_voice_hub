# T-019: log

## 2026-10-30

- Started from origin/main ac36f1f (T-018 merged as PR #27) in a new worktree
  `~/src/piotr_voice_hub-t019`, branch `t-019-announce-busy-stop`.
- Read AGENTS.md, design.md (§4.1 step 8, §6), the tasks README (T-019 row, the P1 → T-017,
  T-019, T-020, T-021 carry-forward) and the T-017 plan. Decisions (plan.md):
  - an announcement is taken only when `idle` with no segment open; `listening` (a wake open)
    is busy;
  - an announcement plays no cue before it, keeps the visual, and plays no timeout cue when
    TTS fails before audio;
  - `stop` with nothing playing is `busy` (the T-017 precedent for `release`);
  - `stop` needs a driver command: `PlaybackStop`, sent to the device as a media player STOP
    with `announcement=True`. Checked in the ESPHome source (dev branch): the `speaker` media
    player (BOX-3) and `speaker_source` (Voice PE) both stop their announcement pipeline for
    it. Not tried on a device (production is in use); it is on the hardware checklist.

- Targeted tests: `test_speak_while_a_wake_is_open_is_busy` timed out because `idle(h)` waits for
  IDLE as the last status while the script goes straight on to LISTENING; it starts the session
  directly now (and lost an unused parameter). The `draining` stop case asserted a cut-short
  stream, which only holds mid-stream. The busy-player driver test set PLAYING before the
  connect's initial IDLE update arrived; it waits for that first.
- Docs: design §4.1 step 8 and §6 (`stop`, busy policy), microphone-protocol.md (`PlaybackStop`
  row, transition, sequencing), tasks README (T-018 merged, T-019 in review, carry-forward).
