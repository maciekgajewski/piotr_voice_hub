# T-007 log

## 2026-10-18

- Branch `t-007-esphome-driver` off `origin/main` @ `c3a8dd6` (T-005 merged).
- Read the old driver @ a299643 (read-only, `git show`): `drivers/box3_esphome.py` is 1327 lines
  and covers connection/recovery, capture with the peak gate, cues/visuals, and playback (an
  HTTP WAV stream per playback plus `media_player` announce and drain, about 400 lines).
  Its tests are 1552 lines. Proposed split: T-007 connection and capture, a new T-014 for
  ESPHome playback. See plan.md. Implementation waits for the split decision.
- Loud non-speech clip: none in this repo. Candidates in the old repo:
  `audio/playback/timer_finished.flac` and `firmware/esphome/sounds/*.wav`.
