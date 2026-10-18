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

## 2026-10-18 (review of `2d6d78c`: MERGE with nits)

Review: https://github.com/maciekgajewski/piotr_voice_hub/pull/4#pullrequestreview-5564321827.
Rebased onto `b9721bc` (T-003 merged). Fixed:
- NaN and infinity are rejected in every numeric field (`math.isfinite`). An infinite timeout
  would mean no timeout.
- A non-UTF-8 file, an unhashable YAML key and YAML syntax errors all give one-line errors
  (exit 2), never tracebacks.
- Duplicate satellite `address` values are rejected, like duplicate ids. The config has no
  name field, and areas may repeat.
- Every error names the file, including an empty file and overrides into a non-mapping section.
  A bad value in `satellites.defaults` is reported as `satellites.defaults.<key>`, not under the
  first device.
- CLI tests assert the root log level that `main()` sets, and a fixture restores the root logger.

Skipped as agreed: making `profiles` read-only, and parsing §7 out of design.md in a test.
