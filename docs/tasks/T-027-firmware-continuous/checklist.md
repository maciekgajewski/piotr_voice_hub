# T-027 hardware checklist (OTA flash and rollback)

Gates changed on 22 Nov 2026 (Maciej, 15:33: "why do we need to back them up? there is
no data on the boxes, just flash them OTA"): OTA only, no USB backup, Maciej need not be
present at the box. Each flash still waits for Maciej's go, relayed by the manager.
The session that used this checklist is in `ota-2026-11-22-results.md`.

## Before a flash session

- [ ] `make check` is green on the ref being flashed; `tools/check_firmware.sh` is green.
- [ ] The manager has relayed Maciej's go for this box. One satellite at a time,
      voice-pe-03 first, then voice-pe-02 only after voice-pe-03 passes.
- [ ] Never change the volume. Capture/recording stays off.
- [ ] Rollback build compiled in advance for this box: the old project's YAML
      (`~maciek/piotr/firmware/esphome`, tree `a299643`) on ESPHome 2026.4.5, with the
      same secrets file. Without the current API key and OTA password in a rollback
      build, the hub cannot reconnect and OTA cannot authenticate.
- [ ] Both satellites idle on the hub's `/healthz`.

## Secrets

Use the existing file `/home/maciek/piotr/firmware/esphome/secrets.yaml` in place,
mounted read-only into the container over the build dir's `secrets.yaml`. Never copy,
print, log or commit it, and never create a `secrets.yaml` in a checkout. Check key
names only. Its per-box API keys must match the hub config (compare, never print).

## Build and flash (OTA)

Build in a private scratch dir outside git (mode 700), with a private cache dir, from
a clean export of the ref being flashed:

```sh
W=~/fw-t027-prep   # scratch, not the repo; build/ holds a copy of firmware/esphome
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$W/build:/config" \
  -v /home/maciek/piotr/firmware/esphome/secrets.yaml:/config/secrets.yaml:ro \
  -v "$W/cache:/cache" -e ESPHOME_CACHE_DIR=/cache -e HOME=/cache \
  -e PLATFORMIO_CORE_DIR=/cache/.platformio \
  ghcr.io/esphome/esphome:2026.4.0 compile /config/voice-pe-03.yaml
getent hosts piotr-voice-pe-03.local      # re-resolve right before the upload
# same docker run, but: upload --device <IP> /config/voice-pe-03.yaml
```

Bridge network (mDNS does not resolve inside the container). The hub keeps running:
the box drops off (liveness warning, forced disconnect) and the hub re-arms with
backoff (1 s doubling, capped at 30 s). An interrupted OTA keeps the old app slot.

## After each satellite

- [ ] Hub log: the reconnect for this box shows `esphome_version=2026.4.0`.
- [ ] Hub log: no `firmware lacks optional services` warning for this box.
- [ ] Hub log: no liveness warnings after the reconnect.
- [ ] Live test with `tools/speak_test.py --listen-after`: wake accepted, accepted cue,
      reply, follow-up ready chime, a follow-up without the wake word accepted, window
      closes cleanly. (The chime needs a hub that calls `FOLLOW_UP_READY`, i.e. main
      after PR #39.)
- [ ] The device does not react to an on-device wake word (there is no
      `micro_wake_word`).

## If something is wrong

Before rolling back, confirm the test was actually spoken during the window (on
22 Nov the first pe-03 window looked like a fail because nobody spoke). Then OTA the
pre-built 2026.4.5 rollback build to that one box (`upload --device <IP>` from its
build dir), and stop. Do not flash the next satellite. Tell the manager. Safe mode
(after repeated boot failures the box comes up with Wi-Fi and OTA only) accepts the
same OTA. USB is the last resort and needs Maciej at the box.

## After the session

- [ ] Delete the build dirs (generated code and binaries contain the keys) and our
      private image tags, by name only. No Docker prune.

## Hub deploy

A production hub deploy needs Maciej's own words, asked for through the manager. On
22 Nov main `833650c` was deployed for the follow-up chime (see the results file).
