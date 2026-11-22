# T-025: log

## 2026-11-22

- Started from origin/main c64ebcf (T-024 merged as PR #35) in a new worktree
  `~/src/wt-docs-followup`, branch `t-025-docs-followup`. Production checkout
  `~/src/piotr_voice_hub` left detached at c64ebcf.
- Session files copied from `~/probe` after a secrets check (no API keys, tokens,
  or IPs beyond what the repo already shows). F-2 in the 22 Nov results is closed
  (not reproduced; Maciej heard the accepted cue clearly at 12:20:52 Europe/Warsaw).
  F-1 remains open: cause under investigation in its own task (likely 24-bit FLAC
  vs the firmware's 16-bit-only player). Process lesson kept.
- Added `tests/data/beep_16k.wav` (0.3 s, 880 Hz, 16 kHz 16-bit mono, 9644 bytes)
  and documented the generator in `tests/data/README.md`. Checklist E5 now uses
  the `$SHA`-pinned raw.githubusercontent.com URL. E1 and E6 are unchanged.
- T-024 PR #35 review nits (docs only): checklist step 0 stops on any failed
  expect; step 6 repeats the `--wait` fallback; T-024 log appends this review
  (earlier "one nit / re-review: clean" line left as history).
- T-023 runbook teardown now passes `--satellite voice-pe-03` and
  `--ws ws://127.0.0.1:8750/ws`, which `parse_args` requires even for `--teardown`.
