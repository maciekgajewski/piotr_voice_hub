# T-004: hub config

## Goal

Load and validate the hub config (design.md §7) strictly, with command-line overrides, behind a
`piotr-voice-hub --config <path>` entry point that only loads the config and logs it.

## Scope

- `src/piotr_voice_hub/config.py`: frozen dataclasses for every §7 section and `load_config()`.
  - Every §7 key is required; no config values hardcoded in code.
  - Satellite timing keys come from `satellites.defaults`; a device may override any of them.
    A timing key missing from both is an error naming the device.
  - Unknown keys, wrong types (bool is not an int), out-of-range values, duplicate satellite ids
    and duplicate YAML keys are errors that name the offending path.
  - `HubConfig.redacted()` hides satellite API keys for logging.
- `src/piotr_voice_hub/cli.py`: `--config` (required), overrides `--log-level`, `--api-host`,
  `--api-port`, `--playback-http-port`. Overrides are applied before validation, so they are
  checked the same way. Invalid config: one clear ERROR line, exit code 2.
- `config/hub.example.yaml`: §7 verbatim, with placeholder API keys.
- Dependency: PyYAML (only).

## Out of scope

- Using the config for anything (satellites, STT, API come in later tasks).
- More CLI overrides than the four above; new config keys not in §7.

## Acceptance

- Unit tests: example config, defaults merged with per-device values, override order, override
  validation, missing path, invalid YAML, invalid values. `make check` and CI pass.
