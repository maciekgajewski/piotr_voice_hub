# T-030: 72 h soak

**Status:** tools merged (PR #45, `4aee1e6`); live soak run 22–25 Nov 2026, FAIL as written
(criteria 3 and 4, finding F1); results in `soak-2026-11-25-results.md`. Branch `t-030-soak-72h`. The code PR ships this procedure and the
log-summary tool. The live run is a separate step, started only on the manager's go.
Decisions (manager, 22 Nov 2026): option A (no API client, no transcript text stored),
thresholds below accepted, production frozen for the 72 h; Maciej notes TV times and real
wakes (Maciej, 20:17).

## Goal

Run the hub unattended for 72 h on all configured satellites, with the TV on in the living
room, and show that it stays up. Count false accepts, reconnects, liveness trips and stuck
states, and measure memory/VRAM drift (design.md §10–§11, P2 exit criterion).

## Setup under test

- Production `833650c` (`piotr-voice-hub-hub-1`, API `ws://localhost:8750/ws`, `/healthz`
  on 8750), plus Piper and speaker recognition.
- Satellites: `voice-pe-02` (living room, TV) and `voice-pe-03` (study). Both run T-027
  firmware (`esphome_version=2026.4.0`). "All satellites" means the satellites in
  `/healthz` at the start. The bedroom Voice PE and the BOX-3 are not configured.
- No consumer is connected. An accepted wake gets the accepted cue, stays `processing`,
  and after `reply_timeout` (30 s) plays the timeout cue and re-arms (§4.1 step 5). This is
  the designed path, and the soak exercises it on every false accept.

## Scope

1. `tools/soak_summary.py`: stdlib only, offline, read-only on a saved hub log. Per
   satellite, and per hour (CET; the log is UTC), it counts:
   - segments, accepts (`wake=True`), candidates not accepted, wake word alone,
     `no request within`, reply timeouts;
   - session `unavailable: …; re-arming` lines, split into liveness (`stalled stream or
     digital silence`), connect failures and other; reconnects (`connected to` after the
     first) and `esphome_version`;
   - `still busy` / `no change within` bound warnings, `lacks optional services`;
   - stuck `processing` (an accept with no reply timeout, release or speak within 35 s);
   - accept latency: the `segment … wake=True` line to `played cue utterance_accepted`;
   - hub-wide: `hub running` (starts), ERROR/CRITICAL lines, tracebacks.

   With `--health health.jsonl` it adds connected % and the longest non-idle run per
   satellite, and counts lines that are not JSON (an HTML error page, no answer).
   With `--notes notes.csv` (Maciej's notes, below) it also gives TV hours, false accepts
   by TV state (on, off, unknown) and per TV-hour, and the real-wake hit rate and latency.
   Text output plus `--json`. Exit 2 on a missing or malformed file. It never connects to
   anything.
2. `tools/soak_monitor.sh`: the two read-only monitors, `log` and `sample` (below).
3. Tests: a synthetic log and notes fixture (`tests/data/soak/`, no IPs, no transcripts)
   give the expected counts; missing or malformed files give exit 2.

## Out of scope

- Any hub, firmware or config change, or deploy. Production stays at `833650c` for all
  72 h.
- Audio recording or capture of any kind. T-023 stays off. No PCM and no voice is stored.
- Sending any API command (`speak`/`play`/`stop`/`release`), and volume changes.
- Cross-satellite arbitration (Maciej, 22 Nov 15:30: no change).

## Production freeze

For the 72 h production stays at `833650c`: no deploy, no config or firmware change, no
flash, no restart, and no other live session or API client (no `speak_test.py`). Anything
that needs production ends the soak first (see Abort).

## Monitors (unattended, read-only)

Run from the worktree in tmux session `t030` as grok-developer, with output in
`~/probe/t030-YYYYMMDD/` (mode 700, outside git). Each window runs under `timeout 73h`:

```sh
D=~/probe/t030-$(date +%Y%m%d); START=$(date -u +%FT%TZ)
tmux new-session -d -s t030 -n log "timeout 73h tools/soak_monitor.sh log $D $START"
tmux new-window -t t030 -n sample "timeout 73h tools/soak_monitor.sh sample $D"
```

| Mode | What | Interval | Output |
|---|---|---|---|
| `log` | `docker logs -f --since $START piotr-voice-hub-hub-1`, rerun after 10 s if it ends (a container restart); each rerun rewrites the file from `$START`, so nothing is duplicated. Rotation is 5 × 10 MB, about 3–10 MB/day today, so 72 h fits. | continuous | `hub.log` |
| `sample` | `curl -m5 /healthz` (connected and state per satellite) | 30 s | `health.jsonl` |
| `sample` | `docker inspect` (StartedAt, RestartCount, Health) and `docker stats --no-stream` for hub, Piper and speaker; `nvidia-smi` MiB per process with its container (from `/proc/PID/cgroup`); probe dir size; `df /` | 5 min | `res.csv` |

- **No `set -e` in the sampler:** a failed step (docker, curl, nvidia-smi, du, df) is
  logged and the loop goes on; a missing guard value counts as 0 and is logged as `missing`
  on every iteration, so the guard is never skipped silently.
- **Size guard:** if the probe dir passes 200 MB or `/` 90 %, the sampler writes
  `ABORT` and kills the `t030` tmux session (our monitors only).
- **No API client.** Nothing connects to the WebSocket API, so the hub runs exactly as
  with 0 clients and the busy policy never comes into play. There is no exec into
  containers, no restart and no prune.

## Privacy

The hub log has no transcript text, only counts, IDs, durations and speaker hints. No API
client runs, so no transcript is stored anywhere. False accepts are classified only from
the log (time, satellite, speaker hint) and Maciej's notes. Nothing from the soak goes into
git except the counts in the results file.

## Maciej's part

- Normal life. He notes the living-room TV on/off times.
- 3–5 real wakes per room per day ("Ryszardzie, która godzina?"), each with the time and
  room. He hears the accepted cue, then the timeout cue 30 s later (no consumer).
- He notes any cue he did not ask for (time, room) and any ring light left on.
- He does not change the volume, mute or flash anything; if he must, he notes the time.
- The study stays quiet at night: the quiet-room check (no liveness trips on voice-pe-03).

The manager forwards the notes; they become `notes.csv` in the probe dir (local times):

```csv
time,event,satellite
2026-11-23 18:00,tv_on,
2026-11-23 18:05,wake,voice-pe-02
2026-11-23 22:30,tv_off,
```

A noted wake is a hit if that satellite accepted within 120 s of the noted time; every
other accept is a false accept. Safety line: for a day with no notes, the TV state is
unknown (false accepts reported per hour) and the real-wake criterion is N/A for that day,
not FAIL.

## Pass criteria (accepted by the manager, 22 Nov 2026)

1. The hub ran all 72 h, with RestartCount and StartedAt unchanged. There are no CRITICAL
   lines, no `event loop stalled` and no tracebacks. Piper and speaker did not restart.
2. No stuck state:
   - every `wake` ends in one final or cancel;
   - `processing` never lasts more than 35 s and `listening` more than 25 s;
   - nothing stays non-`idle` for 2 or more consecutive samples without matching log
     activity;
   - no repeating `still busy`.
3. Connectivity: each satellite is connected in ≥ 99.5 % of samples. Every disconnect
   re-arms within 60 s (the backoff caps at 30 s). Reconnects are ≤ 3 per satellite per
   24 h; more is a finding even if it recovers.
4. Liveness: 0 trips on voice-pe-03 during quiet hours (the T-029 quiet-room check). Any
   trip is followed by a recovery within 60 s.
5. False accepts on voice-pe-02 with the TV on: ≤ 1 per 24 h, also reported per TV-hour
   (from the notes). Candidates that were not accepted are reported but are not a fail.
6. Real wakes (from the notes): ≥ 90 % accepted. Accept latency (segment line to accepted
   cue) has a median ≤ 1.0 s and a max ≤ 2.0 s. The T-022 B2 baseline is 0.51–0.76 s.
7. Drift: the hub's RAM growth from hour 1 to hour 72 is ≤ 10 % and not monotonic. Hub +
   Piper VRAM stays within 4471 ± 100 MiB per process sum (Ollama varies and is excluded).
8. Disk: the probe dir is ≤ 200 MB and no hub log was lost to rotation.

## Abort (only our monitors stop; containers are never touched)

- The hub restarts more than 3 times in 1 h, or the watchdog fires or OOMs. Stop, report
  at once (priority) to the manager. A rollback needs Maciej's words.
- A satellite is offline for more than 30 min. Report it; Maciej may check its power or
  Wi-Fi. The soak goes on and the gap is excluded.
- Maciej asks to stop (for example false-accept cues are annoying), the disk guard trips,
  or another task needs production (the freeze). Any deploy during the soak ends it.

## Report

`docs/tasks/T-030-soak-72h/soak-YYYY-MM-DD-results.md`, all times CET:

- the start and end, the SHA, the firmware versions, TV hours;
- a PASS/FAIL table for criteria 1–8, with the numbers per satellite;
- an hourly false-accept and liveness chart as a text table;
- latencies, restarts and reconnects with times, drift (first, last and max);
- findings, and any deviations (Maciej's notes summarised).

There are no transcripts, no IPs and no config contents. Raw probe files stay on piotr.

## Manual test checklist

- [ ] `make check` green; `python3 tools/soak_summary.py tests/data/soak/hub.log --notes
      tests/data/soak/notes.csv` prints the expected counts.
- [ ] Before the start: production at `833650c`, both satellites `connected`/`idle` with
      2026.4.0, capture off, 0 API clients, freeze agreed, Maciej's go relayed.
- [ ] Start the two monitors and record `$START`; the first `health.jsonl` and `res.csv`
      lines look right.
- [ ] 72 h; a daily glance at `health.jsonl`, `res.csv` and `ABORT` (counts only).
- [ ] At the end: kill tmux `t030`, write `notes.csv`, run `soak_summary.py hub.log --notes
      notes.csv --health health.jsonl --json summary.json`, and fill in the results file.
- [ ] Confirm production is unchanged (SHA, StartedAt, RestartCount).
