# T-004 log

## 2026-10-18

- Branch `t-004-config` off `origin/main` @ `9177b2c` (T-003 / PR #3 not merged yet; independent).
- Decisions: no defaults in code (AGENTS.md); `satellites.defaults` may be partial as long as each
  device ends up with all four timing values; `tts.volume` must be in 0.0–1.0; ports 1–65535;
  beam sizes >= 1; durations > 0. Custom SafeLoader rejects duplicate YAML keys (PyYAML keeps the
  last one silently otherwise).
- CLI overrides limited to log level, API host/port and playback HTTP port. The full config
  (API keys redacted) is logged at DEBUG; INFO gets a one-line summary.
- `make check` passes. Status: pushed, PR to be opened.
