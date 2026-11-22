# T-027 hardware checklist (flash and rollback)

Nothing in this file is a go to flash. Flashing or OTA of a real satellite waits
for Maciej to be present and for the manager to say so first. This PR does not
flash, OTA, or connect to any satellite.

## Before a flash session

- [ ] This PR is merged (or the reviewed head is the one being flashed).
- [ ] `make check` is green.
- [ ] `tools/check_firmware.sh` is green on piotr (`esphome config` for all four
      device YAML files, using only `secrets.example.yaml`).
- [ ] The manager has told Maciej the plan and has an explicit go.
- [ ] Maciej is present. One satellite at a time, starting with voice-pe-03
      (the usual test box), then voice-pe-02, then voice-pe-bedroom and
      box3-satellite if they are in service.
- [ ] Read each device's current volume and mute, and write them down. Restore
      both at the end, including after a rollback.
- [ ] D-capture stays off.

## Current firmware (rollback source)

The satellites today run the old project's firmware from
`~/src/piotr_the_ai_server/firmware/esphome` (read only; do not modify that
tree). Real `secrets.yaml` lives only on Maciej's machine / his copy of that
tree, never in this repo.

Rollback is a reflash of that old tree, with his secrets, over OTA or USB:

```sh
# From a work directory that already has his secrets.yaml (not committed):
# use the old project YAML, not this repo.
esphome upload /path/to/piotr_the_ai_server/firmware/esphome/voice-pe-03.yaml
# USB fallback, if OTA fails: esphome upload --device /dev/ttyACM0 ...
```

Optional but recommended, before the first new flash: compile and keep a
`.bin` of the current (old) firmware in a Maciej-only path, not in git, so a
rollback does not depend on rebuilding.

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
- [ ] Hardware mute and volume still work. Restore the written-down volume and
      mute.
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
