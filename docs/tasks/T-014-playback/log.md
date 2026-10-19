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
