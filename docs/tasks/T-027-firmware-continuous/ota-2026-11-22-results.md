# T-027 OTA flash (voice-pe-03, voice-pe-02) results — 22 Nov 2026

The T-027 continuous-mode firmware (origin/main, `firmware/esphome/`, unchanged since
`42ed871`) was flashed over the air to voice-pe-03 (study) and voice-pe-02 (living
room). Each flash happened only on Maciej's go, relayed by the manager. All times CET
(Europe/Warsaw). Capture/recording OFF throughout, nothing recorded, volume never
changed. The bedroom Voice PE and the ESP32-S3-BOX-3 were not part of this session.

## Result: PASS on both boxes

| Box | Firmware before | Flashed | Reconnect | Live test |
|---|---|---|---|---|
| voice-pe-03 | old project, ESPHome 2026.4.5 | re-flash before 18:17 (first flash 15:54, rolled back 16:09) | `esphome_version=2026.4.0`, no lacks-warning | PASS 18:17 (wake, cue, reply); follow-up PASS 19:06; chime PASS 19:44 |
| voice-pe-02 | old project, ESPHome 2026.4.5, lacks-warning | OTA 19:53:55 (10 s upload, "OTA successful") | 19:54:34, `esphome_version=2026.4.0`, no lacks-warning | PASS 19:58 (wake, cue, reply, chime, follow-up, chime) |

## Decision: OTA only, no backup

Maciej, 22 Nov 2026 15:33: "why do we need to back them up? there is no data on the
boxes, just flash them OTA". This replaced the USB `read_flash` backup and the
"Maciej present" gate in the original checklist:

- OTA only, no backup dump, one box at a time, voice-pe-03 first.
- Each flash only on Maciej's go, relayed by the manager.
- Rollback source: the old project's firmware YAML (`~maciek/piotr/firmware/esphome`,
  tree `a299643`) on ESPHome 2026.4.5, compiled in advance per box. Rollback
  `firmware.ota.bin` is about 2.85 MB; the T-027 one is about 2.24 MB (both well
  inside the 0x7C0000 app slot).

## Procedure (runbook)

1. Secrets: the existing file `/home/maciek/piotr/firmware/esphome/secrets.yaml` is
   mounted read-only in place over the build dir's `secrets.yaml`. It is never copied,
   printed or committed. Its API keys match the hub config per box (checked by
   comparison only), so the hub reconnects; the OTA password is the one the old
   firmware already uses.
2. Build in a private scratch dir (mode 700, outside git), with
   `ghcr.io/esphome/esphome:2026.4.0` (privately tagged), `--user $(id -u):$(id -g)`,
   and a private cache dir (`/cache`, `ESPHOME_CACHE_DIR`, `PLATFORMIO_CORE_DIR`).
   Compile first (`compile /config/voice-pe-0X.yaml`).
3. Before each flash: both satellites idle on the hub's `/healthz`.
4. Re-resolve the target (`getent hosts piotr-voice-pe-0X.local`; on the day pe-03 =
   192.168.0.115, pe-02 = 192.168.0.108), then from the same container setup:
   `upload --device <IP> /config/voice-pe-0X.yaml` (bridge network; mDNS does not
   work inside the container). The hub keeps running; it logs the expected liveness
   warning and a forced disconnect during the reboot and re-arms with backoff
   (1 s doubling, capped at 30 s).
5. Verify in the hub log:
   - the reconnect line for that box shows `esphome_version=2026.4.0`;
   - no `firmware lacks optional services` warning for that box;
   - no liveness warnings after the reconnect;
   - then a live wake test with `tools/speak_test.py --listen-after` (wake, accepted
     cue, reply, follow-up ready chime, follow-up without the wake word).
6. Rollback if the live test fails: the same `upload --device <IP>` with the
   pre-built 2026.4.5 rollback build. An interrupted OTA keeps the old app slot.
7. After the session: delete the build dirs (the generated code and binaries contain
   the keys) and our private image tags, by name only.

Never change the volume. Capture stays off.

## voice-pe-03 (study)

- First flash at 15:54 (reconnect 15:54:47, lacks-warning gone). The test window
  15:55–16:05 looked like a fail (4 segments of 1.9–2.6 s, none matching the wake
  phrase), so pe-03 was rolled back to the 2026.4.5 build at 16:09.
- An offline diff then showed the audio path was identical in the generated code of
  both builds (`i2s_mics` settings; voice assistant source channel 0, 16-bit, gain 1;
  `noise_suppression_level: 0`, `auto_gain: 0 dbfs`, `volume_multiplier: 1`), and
  that ESPHome's audio components do not differ between 2026.4.0 and 2026.4.5. It
  then turned out Maciej never spoke during that window. Not a firmware fault.
- Re-flashed; PASS at 18:17 (wake, accepted cue, reply). Follow-up check PASS at
  19:06.
- Chime gap: production `c64ebcf` never called the `FOLLOW_UP_READY` cue. Main
  `833650c` (includes the PR #39 T-027 hub side) was deployed at 19:42 on Maciej's
  own words at 19:36: "Yes, deploy main to production now". Rollback tag
  `piotr-voice-hub:rollback-c64ebcf`.
  - `docker compose up` needs `HUB_CONFIG=$HOME/hub-wake.yaml` and
    `HUB_USER=$(id -u):$(id -g)` in the environment, or interpolation fails.
  - `tools/check_gpu_cdi.sh piotr-voice-hub-hub-1` (the container name argument is
    required): PASS. VRAM 4471 MiB.
  - Chime check on pe-03 PASS at 19:44 (the chime played twice; Maciej: "the chime is
    in").

## voice-pe-02 (living room)

- Compiled at 19:47. OTA at 19:53:55 (10 s upload, "OTA successful").
- Reconnected at 19:54:34 with `esphome_version=2026.4.0` and the lacks-warning gone
  (before: 2026.4.5 with the warning). The expected liveness warning and forced
  disconnect happened during the reboot.
- Live test at 19:58: wake "Ryszardzie, która jest godzina?" accepted (speaker
  Maciek 0.63), accepted cue, reply, follow_up_ready chime, follow-up "A jaka jest
  pogoda?" accepted without the wake word, a second chime, and the window closed
  cleanly after 8 s. Maciej heard both answers. PASS.

## Whisper mishearings (observations, no fix proposed)

- voice-pe-03: "pogoda" came out as "typu góda" (correct on voice-pe-02).
- T-022 B2 line 19: "Ryszardzie! KUTTURA GODZINA?" (see
  `../T-022-acceptance/b2-2026-11-22-results.md`).

## Open notes

- A stray wake during the listener-swap gap hit the 30 s reply-timeout cue. That is
  the designed behaviour.
- No cross-satellite arbitration (Maciej's decision, 22 Nov 2026 15:30; T-022
  B2-F1).
- `project_name` / `project_version` are not set in the firmware (`esphome: project:`
  is absent); `esphome_version` is what identifies the build in the hub log.
