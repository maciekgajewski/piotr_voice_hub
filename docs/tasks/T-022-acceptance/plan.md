# T-022: acceptance: plan

**Status:** in review (2026-10-31). Branch `t-022-acceptance`, from `origin/main` (cd8c36a,
T-021 merged as PR #30). Planning and docs only; the hardware run is Maciej's.

## Goal

> **T-022 p1-acceptance** (T-015 … T-021): deploy the three-service compose on piotr and run the
> P1 exit criteria, including a real-device playback check: the satellite fetches the URL and
> audio is heard right after the accepted cue, with a fast Piper reply (PR checklist only, no
> code unless a fix is needed). (tasks README)

design.md §11 P1 exit criteria: **on hardware, phrase → transcript with speaker → `speak` reply
→ follow-up answer without the phrase.** This task turns that, and the hardware checks the
earlier tasks deferred to it, into one end-to-end checklist ([checklist.md](checklist.md)),
with the measurements the tasks README asks for.

## Scope

- [checklist.md](checklist.md): the acceptance run on voice-pe-02 (living_room) and voice-pe-03
  (study): every merged P1 feature end to end, the restart and recovery cases, and the
  measurements below. Each step is one copy-paste command (the `speak_test` tool pinned by a
  `<sha>` placeholder, or the `docker` command a recovery case needs), its expected result,
  and what to record.
- PR #30 nits: N3 (the connect check is bounded at about 6 s, not 3 s: design.md §4.1 step 6,
  microphone-protocol.md) and N2 (a test that `close()` stops only a URL, not a TTS stream).
- Tasks README: T-021 merged (PR #30), T-022 in review; T-021 plan status done.

## Out of scope

- Deploying: the checklist's step 0 is Maciej's (the brief: nothing is deployed by the task).
- voice-pe-bedroom and box3-office (the brief names only voice-pe-02 and voice-pe-03), so the
  BOX-3 half of the T-017 playback-end comparison waits for a run that includes the BOX-3.
- The 72 h soak (design §10, P2: after the firmware trim and the audio-liveness check). T-022
  counts false wakes over two shorter periods instead (interpretation 4).
- Any code change, unless the run finds a bug (then its own task and PR).
- A log-summary helper (interpretation 6).

## Interpretations (where the docs leave a choice: the simplest reading)

1. **Two satellites.** The README row says "all four"; the brief narrows the run to voice-pe-02
   and voice-pe-03, and both are Voice PEs. The README row now says so.
2. **The checklist lives in the task directory** (`checklist.md`), and the PR body carries a
   short version with a link. AGENTS.md asks every PR for a manual checklist; this one is
   long, has record tables, and is the run's own record afterwards (Maciej's results go into
   `log.md`).
3. **Pass/fail is functional; numbers are a baseline.** The design gives a pass criterion only
   for the P1 exit (functional) and the VRAM budget (§4.3: hub and Piper about 4.4 GB, up to
   about 4.9 GB with concurrent partials). It gives no number for the wake miss rate, false
   wakes, reply latency or memory. T-022 passes when every functional step behaves as
   expected, VRAM is within the budget, and no container restarts on its own. The rest is
   recorded as the baseline the P2 soak compares against; a result that looks wrong (for
   example several misses on one satellite, any false accept) is written up, not silently
   passed.
4. **Wake miss rate and false wakes (README note "P1 → T-022").** Misses: a fixed list of 20
   addressed utterances per satellite (18 plain, one bare-wake-then-request, one repeated
   phrase), plus 5 that must not wake (other case forms, the vocative at the end). False
   wakes: a 2 h evening period with the TV on next to voice-pe-02 and an overnight quiet
   period of at least 8 h on both, counted from the hub log. That is the README's "fixed set
   of utterances per satellite" and "false wakes over a quiet period", and it also covers the
   design's two hardware revisits (§4.1 step 4: does Whisper keep the vocative ending; does
   the 3 s bare-wake window misfire with the TV on).
5. **Reply latency, from the hub log:** the time from the `segment … wake=True` line (the final
   transcript is in) to `playback … requested by` (the satellite starts fetching the reply).
   It covers the accepted cue, the speaker hint and Piper's first audio; the STT final and
   the device's own buffering are not in it, so Maciej also notes by ear whether the reply
   comes promptly. Five samples per satellite.
6. **No helper script.** Every count is one `docker logs … | grep -c` and the latency is five
   pairs of timestamps per satellite; a tested parser would be more code than the run needs
   (simplicity first). If the P2 soak needs counts over 72 h, it can add one then.
7. **Restarts, VRAM and memory:** `docker inspect` (RestartCount and StartedAt) at the start and
   at the end, `nvidia-smi` per process (matched to the hub and Piper containers), and
   `docker stats` memory at the start and end, so growth over the run shows.
8. **The T-017 playback-end check (README carry-forward)** on Voice PE: a 20 s reply must be
   heard to its last words, and the hub must not hear its own reply (no segment right after
   `played`). The estimate is kept unless that fails.
9. **Recovery cases:** the hub restarted, the hub killed mid-play (the connect check, T-021),
   a satellite power-cycled, the speaker service stopped (hint `null`, one WARNING), Piper
   stopped (the accepted cue, then the timeout cue, never a hang), and the reply timeout. All
   are Maciej's actions on his own deploy; none changes config or satellite settings.
10. **The `play` steps use the LAN http server on the laptop** (as in T-021's checklist), so they
   check the real-device fetch without internet.

## Steps

1. Read design.md and the tasks README for T-022; write this plan.
2. N3 wording, N2 test; README and T-021 status.
3. `checklist.md`; `log.md`.
4. `make check`; push; the PR body with the short checklist.

## Acceptance criteria

- `make check` green.
- The checklist covers the P1 exit on both satellites, wake/STT, speaker hint, announcements,
  busy, stop, release, reply timeout, listen_after follow-up, `play`, and the recovery cases,
  each step with one command, the expected result and what to record.
- Maciej's run (after merge): every functional step as expected, VRAM within §4.3, no
  unplanned restart; the numbers recorded in this task's `log.md`.
