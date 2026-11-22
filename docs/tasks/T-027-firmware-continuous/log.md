# T-027 log

## 2026-11-22

- Numbered in the P2 breakdown. Implementation starts on `t-027-firmware-continuous`.
- No flash or OTA of a real satellite in the code PR.

## 2026-11-22 (implementation)

- Copied `firmware/esphome/` from `~/src/piotr_the_ai_server` (read-only). No `secrets.yaml` was present or copied.
- Trimmed both device packages to continuous mode: removed `micro_wake_word` and models (including the `/home/maciek/piotr/wakeword` path), the wake-sensitivity / wake-engine selects, on-device wake paths (button and `start_follow_up_listening`), HA timers and timer-ringing (scripts, LED effects, display pages), and the cloud-auth error sound.
- Added `play_follow_up_ready_cue` (reuses `sounds/follow_up_listening.wav`). Cue API entries are `api.actions` with `supports_response: status` and `api.respond` after `script.wait`. Listen and visual actions stay fire-and-forget.
- Hub: a follow-up window now plays `FOLLOW_UP_READY` after the `LISTENING` visual and before re-arm (cues stay disarmed). The service remains optional, so an unflashed device still connects and the driver skips the cue with a warning, then emits `CueFinished`.
- Tests: `tests/test_microphone_firmware_conformance.py` (old visual/stop invariants plus no on-device wake word, cue `api.respond`, secrets only via `!secret`). Session follow-up tests expect the ready cue. `make check` green (1002 passed, 8 skipped).
- `tools/check_firmware.sh` runs `esphome config` in `piotr-voice-hub-esphome-t027:2026.4.0` (never `:latest`) with a throwaway copy of `secrets.example.yaml`. Compile was not required for this PR if config validates; a full compile can wait for the flash session.
- Flash and rollback procedure: `docs/tasks/T-027-firmware-continuous/checklist.md`. Nothing was flashed or connected to a satellite. Production containers were not touched.
- `tools/check_firmware.sh` on piotr (ESPHome 2026.4.0, image `piotr-voice-hub-esphome-t027:2026.4.0`, dummy keys only): `esphome config` OK for voice-pe-02, voice-pe-03, voice-pe-bedroom and box3-satellite. A first run failed because the Voice PE `i2s_audio:` header had been dropped with the timer scripts; it was restored. Compile was not run (config is enough for this PR; compile belongs in the flash session).
