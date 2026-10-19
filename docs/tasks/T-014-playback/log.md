# T-014 log (append-only)

## 2026-10-19: start; plan written, stopped for design questions

- Branch `t-014-playback` from origin/main (48b5f70), in the worktree
  `~/src/piotr_voice_hub-t014`. The deployed hub, its main checkout and the satellites were not
  touched: Maciej is running his acceptance test.
- Read design.md (§4, §4.1 step 6, §6, §7, §8, §9), the T-014 entry and the carry-forwards, the
  T-007 driver, `messages.py`/`protocol.py`, the old driver's playback path and the old
  firmware YAML (all read-only). This repo has no firmware yet.
- URL vs streaming is not ambiguous in the design: there is one shared HTTP server, and the
  satellite fetches a URL. The old repo does the same, with one server per playback.
- Stopped after plan.md: Q1 (the `supports_response` cue needs a firmware change), Q2 (drain by
  timing or by media player state) and Q3 (is `play` with a consumer URL in T-014) each change
  the implementation. Recommended: Q1 (b) driver side with a fallback, firmware in P2; Q2 (a)
  timing as in the old repo; Q3 no, P1.

## 2026-10-19: decisions; implemented

- Maciej decided (per the project rules: simplicity, narrow scope, reliability, port what
  worked in the old project):
  - **Q1:** driver side only. The firmware `supports_response` change is a P2 carry-forward.
  - **Q2:** the stream-timing end estimate, as in the old repo. A P1 hardware-test
    carry-forward compares it with the media player's announcing → idle state.
  - **Q3:** `play` with a consumer URL goes to P1 (a README note).

  All three are recorded in plan.md and in the docs/tasks/README.md carry-forwards.
- Implemented as in plan.md: `hub/playback_http.py`, the playback and cue-response parts of
  `satellites/esphome.py`, and the server wiring in `run_hub`. The old driver's choices are
  kept: endless WAV header, volume first with a 0.1 s pause, 5 s fetch wait, 30 s send
  margin, 0.2 s grace, UDP-connect local IP. The differences:
  - one shared server with a path per playback, instead of a server per playback;
  - `PlaybackEnd` returns at once and a task emits `PlaybackFinished`;
  - a device that never fetches makes the driver unavailable, where the old driver only logged
    a warning;
  - at most 300 s of audio per stream.
- New `tests/test_playback.py`: the server, driver playback and cue responses. The fake
  ESPHome client's media player now GETs the announced URL over loopback. The T-007 test
  "playback fails loudly until T-014" is gone. `make check`: 486 passed, 1 skipped.
- Unit tests only. The deployed hub, its checkout and the satellites were not touched
  (Maciej's acceptance test). There is no way to trigger a playback on hardware before the P1
  `speak` command, so the hardware check is in P1.
- docs/tasks/README.md: the T-014 table row is left as is, to avoid a conflict with PR #15,
  which edits the line after it. Its status follows when both are merged.
