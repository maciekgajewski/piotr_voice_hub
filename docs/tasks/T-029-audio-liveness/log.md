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
