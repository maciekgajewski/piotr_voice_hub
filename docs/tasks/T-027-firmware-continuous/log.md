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

## 2026-11-22 (rebase after #38, compile, mute/volume nit)

- PR #38 squash-merged to main as `3b46f75` (`docs: P2 task breakdown (T-027–T-031) (#38)`). This branch was rebased onto that commit (`git rebase --onto origin/main c7d7a9e`).
- Live mute/volume checklist (and the matching plan outline): never change the volume. Read the reported mute and volume first and write them down; confirm the controls exist or are reported. If a real check needs a change, Maciej does it himself and restores it. Restore anything that was changed, including after a rollback.
- `esphome compile` of all four device YAMLs on piotr, image `piotr-voice-hub-esphome-t027:2026.4.0`, throwaway dummy secrets, build dir under `~/probe/t027-esphome-compile` (not the repo). Limited to 4 CPUs. Disk stayed well above 10 GB (646 GB free at start, 640 GB at end). No flash, no OTA, no production or device change, no Docker prune.
- Times (wall clock, including config and container start; PlatformIO link time in parentheses), Europe/Warsaw 13:22–13:36:
  - voice-pe-02: 305 s (246 s) — first, cold cache
  - voice-pe-03: 167 s (145 s)
  - voice-pe-bedroom: 174 s (150 s)
  - box3-satellite: 148 s (126 s)
  - Total: 794 s (~13 min). Too slow for CI; `tools/check_firmware.sh` stays config-only by default. `--compile` is optional and is not wired into CI or `make check`.
- Firmware sizes vs the 16 MB flash app slots (`app0`/`app1` = 0x7C0000 = 8,126,464 bytes each). `firmware.bin` / `firmware.ota.bin` are the same size; factory is a merged image.
  - voice-pe-02 and voice-pe-03: firmware.bin 2,237,008 bytes (27.5% of app0). IDF: Flash 27.5% (2,236,611 / 8,126,464), RAM 17.9% (58,592 / 327,680).
  - voice-pe-bedroom: firmware.bin 2,237,136 bytes (27.5% of app0). IDF: Flash 27.5% (2,236,739 / 8,126,464), RAM 17.9% (58,592 / 327,680).
  - box3-satellite: firmware.bin 3,699,632 bytes (45.5% of app0). IDF: Flash 45.5% (3,699,235 / 8,126,464), RAM 14.4% (47,248 / 327,680).
  - Plenty of headroom in both app slots. No YAML compile errors to fix.
- Dummy secrets were deleted from the probe copy after the compile. The probe build dir can be removed; it is not in git.


## 2026-11-22 (PR #39 review: B1, N1–N6)

- B1: Voice PE cue scripts (`play_message_end_cue`, `play_conversation_timeout_cue`,
  `play_follow_up_ready_cue`) now wait until `external_media_player` is announcing and
  then until it is not, with a 2 s start / 4 s finish timeout (under the hub's 8 s cue
  response bound). A timeout sets `cue_played_ok` false and the shared API action
  responds with `success: false` and `error_message: "cue announcement did not finish"`;
  the hub logs a warning and still emits CueFinished (design.md §4.1). `play_sound`
  stays fire-and-forget for button sounds. `start_open_mic_listening` on PE now waits
  up to 5 s for the media player to be idle, like box3. Box3 cue scripts got the same
  success:false timeout path (finish wait raised from 2 s to 4 s). Conformance test
  `test_cue_scripts_wait_for_the_announcement_to_finish` fails if a PE or box3 cue
  script can return (and therefore `api.respond`) before those waits.
- N1: `test_follow_up_window_without_ready_cue_service_still_listens` — old firmware
  with no ready-cue service logs the skip warning, still emits CueFinished, and the
  follow-up listen proceeds.
- N2: `firmware/esphome/LICENSE` (Apache-2.0) and `NOTICE`, with README attribution
  for HA Voice PE (tag 26.4.0, `e99cb51`), the ESP32-S3-BOX-3 / casita yaml from
  esphome/wake-word-voice-assistants, and the vendored cue WAVs (from the old project,
  first committed as `7a046b3`; not the HA Voice PE sound set).
- N3: removed unused `server_controlled_follow_up_starting` from both packages.
- N4: box3 `voice_assistant` now has `use_wake_word: false`.
- N5: plan.md status is in review (PR #39).
- N6: a USB `esptool read_flash` of the full 16 MB, stored on a Maciej-only path
  (e.g. `~/piotr-firmware-backups/`), is a required pre-flash step. ESPHome OTA
  cannot read firmware back. A rebuild from `~/src/piotr_the_ai_server` with his
  secrets and the running ESPHome version is the fallback only. No flash in this PR.

- Rechecked compile after the review fixes, image `piotr-voice-hub-esphome-t027:2026.4.0`, dummy secrets, cache under `~/probe/piotr-voice-hub-esphome`, 4 CPUs. voice-pe-03 compiled in 290 s, box3-satellite in 150 s (Europe/Warsaw 13:58–14:06). Disk stayed above 10 GB (646 GB free at start, 641 GB at the low point). Build dirs removed after the compile. No flash.

## 2026-11-22 (PR #39 approve nits)

- `.gitignore` (repo root and `firmware/esphome/`): ignore `*.bin`, `*.elf`,
  `*flash*.bin`, and `firmware-backups/`, so a 16 MB `read_flash` backup (it
  holds Wi-Fi and API keys) cannot be committed by accident. `git ls-files
  -ci --exclude-standard` is empty: no already-tracked file is ignored.
- Driver test `test_failed_cue_service_response_still_emits_cue_finished`: a
  status-mode `execute_service` response with `success=False` still emits
  `CueFinished` and logs `cue service ... failed: ...`.

## 2026-11-22 (OTA flash session: voice-pe-03, voice-pe-02)

All times CET. Details and runbook: `ota-2026-11-22-results.md`.

- 15:33 Maciej changed the gates: "just flash them OTA". No USB backup, no presence
  needed; one box at a time, voice-pe-03 first, each flash on his go via the manager.
  Rollback builds compiled in advance from the old project's YAML (`a299643`) on ESPHome
  2026.4.5. `checklist.md` rewritten for the new gates.
- Secrets: the existing `/home/maciek/piotr/firmware/esphome/secrets.yaml`, mounted
  read-only in place; never copied or printed.
- voice-pe-03: first flash 15:54; the 15:55–16:05 window looked like a fail, rolled back
  at 16:09. Offline diff: audio path identical; Maciej had not spoken in that window.
  Re-flashed; PASS 18:17, follow-up PASS 19:06.
- 19:42 main `833650c` deployed (Maciej 19:36: "Yes, deploy main to production now") so
  the hub calls `FOLLOW_UP_READY`; rollback tag `piotr-voice-hub:rollback-c64ebcf`;
  `tools/check_gpu_cdi.sh piotr-voice-hub-hub-1` PASS, VRAM 4471 MiB. pe-03 chime PASS
  19:44.
- voice-pe-02: compiled 19:47, OTA 19:53:55, reconnect 19:54:34 with
  `esphome_version=2026.4.0` and no lacks-warning. Live test 19:58 PASS (wake, cue,
  reply, chime, follow-up without the wake word, chime, clean close).
- Observations: pe-03 "pogoda" heard as "typu góda" (correct on pe-02); a stray wake in
  the listener-swap gap got the 30 s reply-timeout cue (designed). No cross-satellite
  arbitration (Maciej's decision).
- Not flashed: voice-pe-bedroom, box3-satellite.
