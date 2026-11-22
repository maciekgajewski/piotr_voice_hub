# T-030 log

## 2026-11-22

- Numbered in the P2 breakdown. The live 72 h run waits for T-027 to be flashed, T-029 to
  land, and a session with Maciej. The code PR ships only the procedure and the log-summary
  tool.

- Plan: add a quiet-room check on Voice PE with auto_gain at 0 dBFS. A quiet room with no
  speech must not trip T-029 liveness (15 s, RMS < 16 → `MicrophoneUnavailable`); count any
  liveness trips in the logs.

## 2026-11-22 (plan)

- Plan rewritten for the live run: monitors (hub log follower, 30 s /healthz, 5 min
  resources), pass/abort criteria with proposed thresholds, Maciej's part, report format.
  Hub logs carry no transcript text; the optional passive WS client and transcript
  retention (options A/B) wait for the manager's decision. Branch local only, not pushed.

## 2026-11-22 (decisions, code)

- Manager decisions: option A (no API client, no transcript text stored; the passive
  client, its events file and option B are dropped), the proposed thresholds accepted,
  production frozen for the 72 h. TV hours and real wakes stay real criteria: Maciej
  (20:17) will note TV times and do 3–5 real wakes per room per day; a day without notes
  is TV unknown and real-wake N/A, not FAIL.
- Added `tools/soak_summary.py` (stdlib, offline; optional `--notes` CSV for TV-hours,
  real-wake hit rate and latency), `tools/soak_monitor.sh` (`log` and `sample` modes,
  read-only, size guard), and tests on a synthetic fixture in `tests/data/soak/`.

## 2026-11-22 (review 1)

- PR #45 review at 4d5177d: REQUEST CHANGES. B1: under `set -euo pipefail` a failed
  cgroup grep (host process or exited pid) or a transient nvidia-smi/du/df/docker/curl
  failure would end the sampler and its disk guard. Fixed: no `set -e`, each step is
  non-fatal, a missing du/df value counts as 0 and is logged (`missing`) on every
  iteration, and the guard still trips. `tests/test_soak_monitor.py` runs the real script
  with failing stubs (two iterations survive; the guard still aborts and kills only the
  tmux session) plus `bash -n` and shellcheck when installed. Test knobs SOAK_INTERVAL,
  SOAK_RES_EVERY, SOAK_ITERATIONS.
- Nit: `soak_summary.py --health health.jsonl` tolerates and counts bad lines (HTML
  body, no answer); fixture and test added.
