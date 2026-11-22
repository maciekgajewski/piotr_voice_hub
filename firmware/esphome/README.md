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
