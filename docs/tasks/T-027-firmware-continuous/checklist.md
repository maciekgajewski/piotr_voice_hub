# T-027 hardware checklist (flash and rollback)

Nothing in this file is a go to flash. Flashing or OTA of a real satellite waits
for Maciej to be present and for the manager to say so first. This PR does not
flash, OTA, or connect to any satellite.

## Before a flash session

- [ ] This PR is merged (or the reviewed head is the one being flashed).
- [ ] `make check` is green.
- [ ] `tools/check_firmware.sh` is green on piotr (`esphome config` for all four
      device YAML files, using only `secrets.example.yaml`). Optional:
      `tools/check_firmware.sh --compile` on piotr (not in CI; several minutes
      per device).
- [ ] The manager has told Maciej the plan and has an explicit go.
- [ ] Maciej is present. One satellite at a time, starting with voice-pe-03
      (the usual test box), then voice-pe-02, then voice-pe-bedroom and
      box3-satellite if they are in service.
- [ ] Read each device's current volume and mute first, and write them down.
      Do not change the volume. If a later check needs a change, Maciej does
      it himself and restores it. Restore anything that was changed at the
      end, including after a rollback.
- [ ] D-capture stays off.
- [ ] Current firmware backup for this satellite is on the Maciej-only path
      (required; see below). Prefer a USB `read_flash` of the full 16 MB.
      Do not flash until that backup exists.

## Current firmware backup (required before the first flash of each device)

ESPHome OTA cannot read firmware back off the device. Before flashing a
satellite, take a full 16 MB flash image over USB, with Maciej present and
after he has identified the serial device:

```sh
# 16 MB = 0x1000000. Store only on a Maciej-only path, never in this repo.
esptool --chip esp32s3 --port /dev/ttyACM0 read_flash 0x0 0x1000000 \
  ~/piotr-firmware-backups/voice-pe-03-YYYYMMDD-current.bin
```

Use a directory Maciej owns, for example `~/piotr-firmware-backups/` on his
machine. Do not copy the image into this repository, a shared checkout, or
chat logs.

If a USB backup cannot be taken, the fallback is to compile the current
firmware from `~/src/piotr_the_ai_server/firmware/esphome` (read only) with
Maciej's secrets and the ESPHome version the device is already running
(from hub connect logs after T-026), and keep that `.bin` plus a note of
the ESPHome version in the same Maciej-only path. A rebuild is only as
good as the tree and version used; prefer the USB `read_flash` image, and
only use the rebuilt `.bin` if Maciej has accepted that fallback.

Rollback is a reflash of that backup (USB `write_flash` of the
`read_flash` image), or of the old project tree with his secrets over OTA
or USB:

```sh
# From a work directory that already has his secrets.yaml (not committed):
# use the old project YAML, not this repo.
esphome upload /path/to/piotr_the_ai_server/firmware/esphome/voice-pe-03.yaml
# USB fallback, if OTA fails: esphome upload --device /dev/ttyACM0 ...
# Or restore the read_flash image:
# esptool --chip esp32s3 --port /dev/ttyACM0 write_flash 0x0 \
#   ~/piotr-firmware-backups/voice-pe-03-YYYYMMDD-current.bin
```

## Flash (OTA, USB if OTA fails)

Secrets stay in a local `firmware/esphome/secrets.yaml` copied from
`secrets.example.yaml` and filled by Maciej. That file is gitignored and must
not be printed, logged, or copied off the machine.

```sh
# After tools/check_firmware.sh has passed, with his secrets.yaml in place:
esphome upload firmware/esphome/voice-pe-03.yaml
```

Do not use `--device` against a production host path until Maciej has
identified the serial device. Do not flash more than one satellite until the
checks below pass on the first.

## After each satellite

- [ ] The hub reconnects (no production compose change is required if the
      running hub already treats `play_follow_up_ready_cue` as optional).
- [ ] Connect logs show the new firmware and that `play_follow_up_ready_cue` is
      present (after T-026, `esphome_version` / `project_version` too).
- [ ] Accepted cue is heard, a timeout cue is heard, and a `listen_after` reply
      plays the follow-up ready cue, then a follow-up without the wake phrase
      is accepted.
- [ ] Continuous listening still works (wake phrase accepted while idle).
- [ ] Hardware mute and volume: confirm the mute control exists and a volume
      level is reported. Do not change the volume. If a real check needs a
      change, Maciej does it himself and restores it. Restore anything that
      was changed, including after a rollback.
- [ ] The device does not react to an on-device wake word of its own (there is
      no `micro_wake_word`).

## If something is wrong

Roll back that one satellite from the old project tree as above, restore
volume and mute, and stop. Do not flash the next satellite. Tell the manager
what failed.

## Hub deploy

`play_follow_up_ready_cue` stays optional in the driver until every in-service
satellite has the new firmware, so an unflashed device still connects and the
driver skips the ready cue with a warning. A production hub deploy still needs
Maciej's own words, asked via the manager, and is not part of this PR.
