# T-027: firmware trimmed to continuous mode

**Status:** implementation on `t-027-firmware-continuous` (draft PR). Based on `p2-task-breakdown`. No flash.

## Goal

The hub owns the Voice PE and ESP32-S3-BOX-3 firmware in this repo, trimmed to continuous
listening (design.md §8). Cue actions answer with `api.respond` once the sound has played, and
`play_follow_up_ready_cue` exists so a follow-up window can play the ready sound.

## Scope

- Copy `firmware/esphome/` from `~/src/piotr_the_ai_server` (read-only) into this repo.
- Keep: API encryption, `voice_assistant` with `start_continuous`, the announce media player,
  LEDs/display driven by server visual states plus the firmware-owned `ERROR` state on
  disconnect, the hardware mute switch and volume, and API services
  `start_open_mic_listening`, `stop_listening`, `reset_open_mic_wake_candidate`,
  `set_visual_idle/listening/processing`, `play_message_end_cue`,
  `play_conversation_timeout_cue`.
- Add `play_follow_up_ready_cue`, reusing `sounds/follow_up_listening.wav`.
- End each cue action with `api.respond` after `script.wait`, in status mode (README
  carry-forward, PR #16 B2). Listen and visual services stay fire-and-forget.
- Remove: `micro_wake_word` and all models (`ryszardzie`, `okay_nabu`, `stop`), the
  wake-sensitivity select, the on-device wake-word path, `start_follow_up_listening`, HA
  timers and timer-ringing, and cloud-auth error sounds. The absolute path
  `/home/maciek/piotr/wakeword/...` goes with the model.
- Secrets only via ESPHome `!secret` and a `secrets.example.yaml`. Never commit `secrets.yaml`.
- Copy and extend `tests/test_microphone_firmware_conformance.py`: assert no `micro_wake_word`
  remains, the new cue is declared with `api.respond`, and `start_follow_up_listening` is gone.
- Hub-side: once the service is in the repo firmware, play `FOLLOW_UP_READY` after playback
  when `listen_after` is set (T-020 currently skips it). Promote the service from optional to
  required, or keep it optional only until the devices are reflashed.
- A CI-compilable check (`esphome config`, and `esphome compile` in a privately tagged
  container if disk allows). Validation must not need real secrets.

## Out of scope

- Flashing or OTA of any real satellite (a later live session, Maciej present, manager told
  first). The flash and rollback procedure lives in the task checklist only.
- Changing production containers, firewall, or recording.
- T-028 fixtures, T-029 liveness, T-030 soak, T-031 docs beyond this task's firmware notes.

## Approach

Copy the four device YAML wrappers and the two device packages plus the shared API-services
package and sounds. Trim in place; do not rewrite the hardware, LED, or media-player blocks.
Switch cue entries from the old `api.services` form to `api.actions` with
`supports_response: status` and `api.respond`. Drop `start_follow_up_listening` from the
shared package (the hub must never call it). Keep Wi-Fi, API keys and OTA passwords as
`!secret` references, with placeholders only in `secrets.example.yaml`.

## Tests

- Firmware conformance: no `micro_wake_word`; no wake-model paths; no
  `start_follow_up_listening`; cue actions declare status-mode `api.respond`; required
  services and `play_follow_up_ready_cue` are present; existing visual/stop invariants from
  the old test still hold.
- Hub session: a follow-up window plays `FOLLOW_UP_READY` when the service is present, and
  still skips it with a warning when it is not (devices on old firmware).
- `esphome config` (or compile) on each device YAML with a throwaway secrets file copied from
  the example, never from a real `secrets.yaml`.

## Manual (hardware) checklist outline

- [ ] `make check` and the firmware validation are green. Nothing flashed.
- [ ] Flash and rollback procedure written (current firmware backup, USB/OTA order, how to
      restore, one device at a time, volume and state restored).
- [ ] Live flash, only later, with Maciej present: one satellite, then accepted / timeout /
      follow-up-ready cues, continuous listen, mute/volume, and rollback once.

## Risks

- Declaring `supports_response` without `api.respond` stalls the driver for 8 s and drops the
  connection (PR #16 B2). Tests must catch that.
- Trimming too much can break LEDs, the mute switch, or announce playback. Keep those blocks.
- A required `play_follow_up_ready_cue` would refuse connect to devices not yet reflashed.
  Keep the service optional until all four are updated, or flash before deploying the hub
  change.
- ESPHome `services` vs `actions` syntax depends on the firmware's ESPHome version; validate
  against the version these devices already run, recorded from hub logs if present (T-026).

## Dependencies

- T-014 (cue / media-player wait) and T-020 (follow-up window, cue currently skipped).
- Blocks T-030 (reflash before the soak). Does not block T-028 or T-029.
