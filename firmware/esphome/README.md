# Satellite firmware (continuous mode)

ESPHome YAML for the Voice PE and ESP32-S3-BOX-3 satellites the hub owns.
Validate with `tools/check_firmware.sh` (config only by default, example
secrets, a privately tagged ESPHome image). `tools/check_firmware.sh --compile`
builds all four device YAMLs the same way; it takes several minutes per device
and is not part of CI. Do not flash from this tree until Maciej is present and
the manager has given a go; the procedure is in
`docs/tasks/T-027-firmware-continuous/checklist.md`.

Copy `secrets.example.yaml` to `secrets.yaml` (gitignored) and fill it in
locally. Never commit, print or log the real file.

## Licence and attribution

The Voice PE and ESP32-S3-BOX-3 YAML in this directory is derived from
Apache License 2.0 upstream projects. The Apache-2.0 text is in `LICENSE`;
the notices below are also in `NOTICE`.

- Home Assistant Voice PE (`packages/home-assistant-voice-pe-piotr-standard.yaml`,
  plus the remote button/jack sounds it still fetches):
  https://github.com/esphome/home-assistant-voice-pe, tag 26.4.0
  (commit `e99cb5147ea4c6f2896772f61fc8bd29612026a7`). The
  `external_components` pin and the `.../raw/26.4.0/sounds/...` URLs use
  that tag.
- ESP32-S3-BOX-3 (`packages/esp32-s3-box-3-ryszardzie.yaml`, plus the
  casita illustrations it fetches):
  https://github.com/esphome/wake-word-voice-assistants (Apache-2.0). The
  exact upstream commit the old project first copied is not recorded.

This tree trims those configs to continuous listening (no on-device wake
word, no HA timers), adds `play_follow_up_ready_cue`, and makes cue
actions wait for the announcement to finish before `api.respond`.

Vendored cue WAVs in `sounds/` (`message_end.wav`,
`follow_up_listening.wav`, `conversation_timeout.wav`) come from the old
project `~/src/piotr_the_ai_server/firmware/esphome/sounds/` (first
committed there as `7a046b3`, 2026-05-26). They are short project clips,
not the Home Assistant Voice PE sound set. No separate third-party
licence was found for them.

