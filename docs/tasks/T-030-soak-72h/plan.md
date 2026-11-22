# T-030: 72 h soak

**Status:** planned. Branch `t-030-soak-72h`. The code PR ships this procedure and the
log-summary tool. The live run is a separate step, started only on the manager's go.

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

1. `tools/soak_summary.py`: stdlib only, read-only on a saved hub log file. For each
   satellite and each hour it counts:
   - segments, wake candidates, accepts (`wake=True`), cancels, `no request within`,
     reply timeouts;
   - `unavailable:` warnings, split into liveness (`stalled stream or digital silence`)
     and other reasons;
   - `connected to`, `connect to … failed`, firmware version lines, `lacks optional
     services`;
   - cue/playback bound warnings (`still busy`, `no change within`);
   - every ERROR/CRITICAL line, tracebacks, `event loop stalled`, `hub running`
     (restarts);
   - accept latency: segment end (the `segment … wake=True` line) to `played cue
     utterance_accepted`.

   Times are converted from the log's UTC to CET. The output is a text table plus JSON.
   The tool exits non-zero on a missing or malformed file and never connects to anything.
2. A procedure (below) and the shell snippets for the monitors. These live in this
   directory, not in `hub/`.
3. Tests: a synthetic log fixture, with no IPs, no transcripts and no household data,
   gives the expected counts. A malformed path gives a non-zero exit.

## Out of scope

- Any hub, firmware or config change, or deploy. Production stays at `833650c` for all
  72 h.
- Audio recording or capture of any kind. T-023 stays off. No PCM and no voice is stored.
- Sending any API command (`speak`/`play`/`stop`/`release`), and volume changes.
- Cross-satellite arbitration (Maciej, 22 Nov 15:30: no change).

## Monitors (unattended, read-only)

Everything runs in tmux session `t030` as grok-developer, with output in
`~/probe/t030-YYYYMMDD/` (mode 700, outside git). Each window is wrapped in
`timeout 73h`, so nothing outlives the soak.

| Window | What | Interval | Output |
|---|---|---|---|
| `log` | `docker logs -f --since $START piotr-voice-hub-hub-1 > hub.log`, in a loop with a 10 s pause. It re-runs from `$START` after a container restart, so nothing is duplicated or lost. Rotation is 5 × 10 MB; today's rate is about 3–10 MB/day, so 72 h fits. | continuous | `hub.log` |
| `sample` | `curl -m5 /healthz` (connected and state per satellite) | 30 s | `health.jsonl` |
| `sample` | `docker inspect` StartedAt, RestartCount and Health for hub, piper and speaker; `docker stats --no-stream` memory and CPU; `nvidia-smi` total and per-process MiB for the hub and Piper PIDs (mapped through `/proc/PID/cgroup`); `du -sh` of the probe dir; `df /` | 5 min | `res.csv` |
| `events` (optional, manager decides; see Privacy) | A passive WS client (`tools/soak_events.py`, via `uv run` from the worktree) that only reads, reconnects with 1–30 s backoff, and writes one line per event | live | `events.jsonl` |

- **Size guard.** The sampler stops every window and writes `ABORT disk` if the probe dir
  goes over 200 MB or `/` is over 90 %.
- **Effect of the passive client.** It uses one of the 16 client slots, and the hub
  broadcasts to it. If it falls behind, only the client is dropped (1008); the hub is not
  blocked. It never sends a command, so the busy policy never applies to it, and sessions
  behave exactly as with 0 clients. A wake still times out at 30 s. Only one other client
  may connect during the soak, and it may not speak: Maciej's note sessions use no client.
- **Production is not touched.** There is no exec into containers (`/healthz` and docker
  read-only calls only), no restart and no prune.

## Privacy (manager or Maciej decides)

- The hub log has no transcript text, only counts, IDs, durations and speaker hints. The
  API carries text only for wake candidates: `transcript_partial` only when it matches,
  and `transcript_final` only on an accept. With no consumer there are no follow-up
  windows, so non-wake TV or room speech never reaches a client.
- An accepted false wake can still carry a few words of TV or household speech. Options:
  - **A (minimal):** no WS client; only logs and samples. False accepts are classified
    from the speaker hint and Maciej's notes.
  - **B (proposed):** the WS client stores event type, satellite, time, `utterance_id`,
    `follow_up`, speaker and `audio_ms`, and the text of `transcript_final` only. It keeps
    no partials and no `raw_text`. The file stays on piotr (mode 600) and is deleted after
    the report. The report gives counts and categories (TV, room, Maciej), never the text
    itself.
- Nothing from the soak goes into git except the counts in the results file.

## Maciej's part

- Normal life. The TV in the living room is on as usual (ideally ≥ 4 h/day), with rough
  on/off times in a note.
- Optionally, 3–5 real wakes per day per room ("Ryszardzie, która godzina?"), with the
  time and room noted. He hears the accepted cue, then the timeout cue 30 s later (no
  consumer).
- He notes any cue he did not ask for (time and room) and any ring light left on.
- He does not change the volume, mute or flash anything. If he must, he notes the time.
- The study stays quiet at night. That is the quiet-room check: no liveness trips on
  voice-pe-03.

## Pass criteria (thresholds proposed; manager or Maciej confirm)

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
5. False accepts on voice-pe-02 with the TV on: ≤ 1 per 24 h (reported per TV-hour).
   Wake candidates that were cancelled are reported but are not a fail.
6. Real wakes (Maciej's notes): ≥ 90 % accepted. Accept latency (segment end to accepted
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
  or another task needs production. Any deploy during the soak ends it.

## Report

`docs/tasks/T-030-soak-72h/soak-YYYY-MM-DD-results.md`, all times CET:

- the start and end, the SHA, the firmware versions, TV hours;
- a PASS/FAIL table for criteria 1–8, with the numbers per satellite;
- an hourly false-accept and liveness chart as a text table;
- latencies, restarts and reconnects with times, drift (first, last and max);
- findings, and any deviations (Maciej's notes summarised).

There are no transcripts, no IPs and no config contents. Raw probe files stay on piotr and
`events.jsonl` is deleted.

## Manual test checklist

- [ ] `make check` green; `soak_summary.py` gives the expected counts on the fixture.
- [ ] Before the start: production at `833650c`, both satellites `connected/idle` with
      2026.4.0, capture off, 0 API clients, no other session scheduled, Maciej's go
      relayed.
- [ ] Start the tmux monitors and record `$START`; the first samples look right.
- [ ] 72 h; daily glance at `health.jsonl`/`res.csv` (counts only).
- [ ] At the end: stop tmux, run `soak_summary.py hub.log`, and fill in the results.
- [ ] Delete `events.jsonl`, if present. Confirm production is unchanged.
