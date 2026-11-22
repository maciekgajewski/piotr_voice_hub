# T-029 log

## 2026-11-22

- Numbered in the P2 breakdown. Must merge before the T-030 soak. No live fault injection
  in the code PR.

- Implementation: while armed and the device stream is running, no live audio for
  `AUDIO_LIVENESS_SECONDS` (15 s, a named constant; design.md does not define a config key)
  raises `MicrophoneUnavailable` once. "Live" means a 16-bit PCM frame with RMS at or above
  `DIGITAL_SILENCE_RMS` (16). No frames, and all-zero or near-zero frames, count as dead;
  a quiet-room noise floor (the T-028 `noise_only` fixture, Gaussian sigma 400) does not.
  The existing session path then applies: status `error` (`connected=false`), `/healthz` still
  200, close, backoff, and re-arm. The timer is cancelled when listening stops, so it does
  not run during playback (disarmed, MP-AUDIO-001). No other detectors were added.
- Tests use a fake clock, so they do not wait the real 15 s.

- Branched from `origin/main` at `362ffe0` (T-028 merged). The quiet-room and all-zero
  tests use the T-028 `noise_only` and `silence` fixtures.

- PR #41 review on 35b875d: APPROVE, no blockers. Nits addressed: `close()` now cancels the
  liveness watch before `await _drop_client()` (with a test); `frame_is_live` decodes samples
  as explicit little-endian `struct` `'<h'`; the P2 intro in `docs/tasks/README.md` matches
  the merged T-027 / T-028 statuses; design.md notes that a muted zeroed stream trips
  liveness after 15 s and then backs off up to 30 s, and that telling mute apart from a
  dead mic is out of scope. The T-030 plan was not edited.
