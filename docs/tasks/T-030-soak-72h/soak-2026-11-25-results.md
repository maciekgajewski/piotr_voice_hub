# T-030 72 h soak results (22–25 Nov 2026)

The soak ran on production `833650c` with both configured satellites. All times are CET
(Europe/Warsaw); piotr and the hub log use UTC. Nothing was recorded, no API client was
connected, the volume was never changed, and production was frozen for the whole window.
The monitors were `tools/soak_monitor.sh` (`log` and `sample`) and the summary was
`tools/soak_summary.py`, both at `4aee1e6`. The raw files stay on piotr in
`~/probe/t030-20261122/`, outside git.

## Result: FAIL as written (criteria 3 and 4); no hub fault

The hub never crashed: no ERROR or CRITICAL lines, no watchdog, RestartCount 0, no stuck
state. The fail comes from repeated audio-liveness trips (finding F1 below), which also
drive the reconnect count over its limit. Criteria 5 and 6 are N/A because no TV or
real-wake notes reached the developer.

| | Time (CET) |
|---|---|
| Start | 22 Nov 20:45:45 (19:45:45Z) |
| Resumed after the outage | 24 Nov 07:11:24 (06:11:24Z), continuing this run with the gap accepted (Maciej, 24 Nov 07:11) |
| End | 25 Nov 20:45:45; the monitors stopped through their `timeout 73h`, and tmux `t030` is gone |

## Gaps

The host was powered off from 22 Nov 23:53:39 to about 08:23 on 23 Nov (about 8.5 h), with
an orderly shutdown from the power button; the hub logged `stopped by signal`. A second
orderly stop came on 23 Nov at 22:09:00 (the hub logged `stopped by signal`; the host boot
time is 22:09:36, kernel 7.0.0-44). We do not know the cause of either beyond these
lines.

- **No samples** from 23 Nov 08:23 to 24 Nov 07:11. The `sample` and `log` monitors
  did not survive the first power-off, so there are no `/healthz` or resource samples for
  that time.
- **Hub log still complete.** `docker logs` keeps a container's log across host reboots
  (it is the same container), so `hub.log`, taken at resume with `--since START`, has the
  hub's lines for the whole window except the power-off. That includes the 23 Nov
  08:23–22:09 instance, which was thought lost. `hub-boot1.log` (the pre-outage copy) is
  contained line for line in `hub.log`, so the summary ran on `hub.log` alone
  (`hub-all.log` is a copy of it, to avoid counting 20 lines twice).
- **Continue, not restart.** Maciej chose on 24 Nov at 07:11 to continue this run and
  accept the gap, rather than start a fresh 72 h clock. A fresh run started on an
  earlier relay was stopped after about 40 s; its directory has been deleted.

Coverage:
- Hub log: about 63.5 of 72 h.
- `/healthz` and resource samples: about 40.7 h (22 Nov 20:45–23:53 and 24 Nov
  07:11–25 Nov 20:45).

## Production (read-only check, 25 Nov 20:52)

- **Checkout:** `~/src/piotr_voice_hub` at `833650c`, clean. Image
  `piotr-voice-hub:latest` = `26df0492eca9`.
- **Containers:** hub, Piper and speaker recognition are all `healthy` with RestartCount
  0. StartedAt is `2026-11-23T21:11:25Z` (22:11:25 CET) for all three, from the second
  boot, unchanged since the resume check on 24 Nov 07:10. The two StartedAt changes in
  the window are the host reboots, not container restarts.
- **`/healthz`:** voice-pe-02 and voice-pe-03 both connected and idle.
- **Firmware:** both satellites show `esphome_version=2026.4.0` on every connect.
- **VRAM:** 4473 / 16311 MiB in total; hub plus Piper 4450 MiB.
- **Disk:** `/` 66 % (619 GB free). The probe dir is 2.2 MB.

## Summary numbers (`summary.json`)

Hub log: 2776 lines, from 22 Nov 21:01:56 (the first line after START) to 25 Nov 20:44:47.
It contains 2 `hub running` lines, at 23 Nov 08:23:29 and 22:11:33 (the two boots), 0
ERROR/CRITICAL lines and 6 traceback lines.

| | voice-pe-02 (living room) | voice-pe-03 (study) |
|---|---|---|
| Speech segments | 993 | 1305 |
| Accepts | 0 | 1 |
| Liveness trips (`no live audio for 15 s`) | 44 | 23 |
| Connect failures | 1 | 1 |
| Reconnects (`connected to` after the first) | 45 | 24 |
| Stuck `processing` | 0 | 0 |
| Connected in `/healthz` samples (4802 samples, 0 bad lines) | 99.94 % | 99.92 % |
| Longest run of non-idle samples | 1 | 1 |

- **The one accept:** voice-pe-03, 23 Nov 14:33:53. "Ryszardzie" alone, then the request
  within 3 s. Speaker hint Maciek 0.61, accepted cue 0.12 s after the segment line, ended
  by the 30 s reply timeout (no consumer). It is probably a real wake, but with no notes
  the tool counts it as a false-accept candidate with the TV state unknown.
- **The 6 traceback lines** are 3 WARNING `graceful disconnect failed; forcing it` events
  logged with `exc_info` (at 24 Nov 05:30 and 25 Nov 03:17), each with a chained
  traceback. They are handled; the hub went on and reconnected.

## Criteria

| # | Criterion | Result | Why |
|---|---|---|---|
| 1 | Hub up 72 h, no restarts, no CRITICAL or tracebacks | **FAIL as written; no hub fault** | RestartCount 0, no CRITICAL or watchdog. StartedAt changed only at the two host reboots, both clean `stopped by signal`. The 6 traceback lines are the handled forced-disconnect warnings above. |
| 2 | No stuck state | PASS | 0 stuck `processing`; the longest non-idle run is 1 sample; the one wake ended in its reply timeout; no `still busy`. |
| 3 | ≥ 99.5 % connected, re-arm ≤ 60 s, ≤ 3 reconnects / satellite / 24 h | **FAIL** | Connected 99.94 % and 99.92 %, and every trip recovered within 19 s, but there were about 16 reconnects per 24 h on voice-pe-02 and 8 on voice-pe-03 (excluding the boot connects). Without the three F1 clusters it is about 1.1 and 1.5 per 24 h. |
| 4 | 0 liveness trips on voice-pe-03 in quiet hours; recovery ≤ 60 s | **FAIL** | Two night trips (24 Nov 05:30 and 25 Nov 03:17), both at the same second as voice-pe-02, so a shared cause, not a quiet-room trip (F2). There is also a daytime loop (F1). Recovery was ≤ 19 s every time. |
| 5 | False accepts on voice-pe-02 with the TV on ≤ 1 / 24 h | **N/A** | No TV notes, so TV-hours are unknown. voice-pe-02 had 0 accepts in the whole window, and the only accept anywhere (voice-pe-03) was probably real. |
| 6 | Real wakes ≥ 90 %, latency median ≤ 1.0 s, max ≤ 2.0 s | **N/A** | No real-wake notes. The one accept had a latency of 0.12 s. |
| 7 | Hub RAM growth ≤ 10 % and not monotonic; hub + Piper VRAM 4471 ± 100 MiB | PASS | Hub RAM in the last instance went from 681 to 735 MiB (+7.9 %, max 739), flat after the first hours; 6-slice means 707, 724, 724, 723, 726, 728 MiB. Piper 1181 MiB, speaker 446 MiB, both flat. Hub + Piper VRAM 4450–4546 MiB across all 481 samples. |
| 8 | Probe dir ≤ 200 MB, no hub log lost to rotation | PASS | 2.2 MB; `/` 64–66 %. The hub log is complete from START except the power-off. |

## Findings

- **F1: liveness loops on a live satellite.** voice-pe-03 on 25 Nov 09:38–09:49 (19
  trips), and voice-pe-02 on 25 Nov 13:35–13:47 (26 trips) and 16:05–16:19 (15 trips).
  - Each cycle is the same: 15 s with no live audio, `unavailable`, close, reconnect
    within 2–3 s, arm, and again 15 s later.
  - The causes T-029 names are digital silence or a muted mic, or a very quiet room
    with `auto_gain` 0 dBFS below the RMS 16 floor. The hub log cannot tell these apart.
  - Design §4.1 says a muted satellite makes the session "back off (up to 30 s)". In
    practice it re-arms after 1 s every time, because each arm succeeds and resets the
    backoff. That is about 3.5 API reconnects a minute while the condition lasts.
  - For the manager or Maciej to decide: was a mute switch used at those times? Should
    the backoff survive a re-arm that trips again within 15 s, or the floor change? A
    change needs its own task.
- **F2: simultaneous trips on both satellites.** On 24 Nov at 05:30:28 both streams
  stopped at the same moment and voice-pe-02 needed a forced disconnect. On 25 Nov at
  03:17:23 both stopped, then both connects timed out at 03:17:39. Both recovered within
  19 s. A common cause such as Wi-Fi or the network is likely; we cannot read more from
  the hub.
- **F3: `soak_summary.py` nits (carry-forward, no tool change in this PR).**
  - It treats the Piper logger's `[pl_PL-bass-high]` suffix as a satellite, so an empty
    `pl_PL-bass-high` row appears.
  - It counts each chained traceback separately, so it reports 6 for 3 events.
  - `hub-boot1.log` was not needed, because `hub.log` already holds the whole container
    log; the procedure can drop that copy.

## Manual test checklist (read-only, done 25 Nov 20:52)

- [x] tmux `t030` is gone and no `soak_monitor` process is left.
- [x] Production `833650c`, image `26df0492eca9`; hub, Piper and speaker healthy, with
      RestartCount 0 and StartedAt 23 Nov 22:11:25.
- [x] `/healthz`: both satellites connected and idle; VRAM 4473 MiB; disk 66 %.
- [x] `python3 tools/soak_summary.py ~/probe/t030-20261122/hub-all.log --health
      ~/probe/t030-20261122/health.jsonl --json ~/probe/t030-20261122/summary.json`
      (no `--notes`: no notes) gives the numbers above.
- [ ] Manager or Maciej: decide on F1 (mute or quiet room; backoff) and whether to rerun
      the soak, with notes, after any change.
