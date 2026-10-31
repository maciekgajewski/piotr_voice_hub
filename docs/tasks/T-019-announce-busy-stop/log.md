# T-019: log

## 2026-10-30

- Started from origin/main ac36f1f (T-018 merged as PR #27) in a new worktree
  `~/src/piotr_voice_hub-t019`, branch `t-019-announce-busy-stop`.
- Read AGENTS.md, design.md (§4.1 step 8, §6), the tasks README (T-019 row, the P1 → T-017,
  T-019, T-020, T-021 carry-forward) and the T-017 plan. Decisions (plan.md):
  - an announcement is taken only when `idle` with no segment open; `listening` (a wake open)
    is busy;
  - an announcement plays no cue before it, keeps the visual, and plays no timeout cue when
    TTS fails before audio;
  - `stop` with nothing playing is `busy` (the T-017 precedent for `release`);
  - `stop` needs a driver command: `PlaybackStop`, sent to the device as a media player STOP
    with `announcement=True`. Checked in the ESPHome source (dev branch): the `speaker` media
    player (BOX-3) and `speaker_source` (Voice PE) both stop their announcement pipeline for
    it. Not tried on a device (production is in use); it is on the hardware checklist.

- Targeted tests: `test_speak_while_a_wake_is_open_is_busy` timed out because `idle(h)` waits for
  IDLE as the last status while the script goes straight on to LISTENING; it starts the session
  directly now (and lost an unused parameter). The `draining` stop case asserted a cut-short
  stream, which only holds mid-stream. The busy-player driver test set PLAYING before the
  connect's initial IDLE update arrived; it waits for that first.
- Docs: design §4.1 step 8 and §6 (`stop`, busy policy), microphone-protocol.md (`PlaybackStop`
  row, transition, sequencing), tasks README (T-018 merged, T-019 in review, carry-forward).

- Review round 1 (before the PR): `tools/speak_test.py` gets T-019 modes, still websockets plus
  the standard library: `--announce SATELLITE` (one speak now, no wake; exit 0 after its
  `playback_finished`, 1 on an error reply), `--stop SATELLITE` (one stop; exit 0 on any answer,
  `busy` expected when nothing plays), `--stop-after SECONDS` (stop that long after each speak
  ack, in reply or announce mode) and `--long` (a Polish text of 348 characters, about 20–25 s;
  estimated, Piper not run here). Existing options and the reply behaviour are unchanged; option
  clashes are argparse errors. 12 new tool tests against a fake hub. Checklist: production has
  only voice-pe-02 (living_room) and voice-pe-03 (study); box3-office dropped.

- PR #28 review round 1 (head 176db23):
  - S1: design §6 said clients send their next item after `playback_finished`, but it goes out
    before the re-arm, so that `speak` is `busy (speaking)`. Code unchanged; §6 now says to wait
    for the `idle` status. Test (reply and announcement): a `speak` sent just after
    `playback_finished` is `busy`, one sent after the `idle` status is taken. The test harness
    got an `on_event` hook for a consumer's reaction.
  - S2: tests for a stop whose `PlaybackStop` fails as the satellite disconnects (mid-stream and
    draining, reply and announcement) and for an announcement acked in the same step as a
    disconnect. Both end with one `playback_finished`, then `error` and the re-arm, and nothing
    hangs; no bug found (`_recover` ends the open playback in both).
  - N1, N2: microphone-protocol.md and design §6 say "for a reply" (`PROCESSING` during
    playback, the timeout cue on a TTS failure).
  - N3: tasks README T-018 row "merged (PR #27)"; T-018 plan.md status done.
  - N4: the busy-while-accepting test the plan promised: speak, release and stop are `busy`
    while the speaker hint is pending, with and without a partial (without one the status is
    still `idle`, and the speak is still no announcement); the release after it works.
  - N5: `tools/speak_test.py`: `--stop` exits 1 on an error reply (0 on its ack);
    `--stop-after` must be > 0 (0 is an option error); `--announce` gives up after 120 s
    (`ANNOUNCE_TIMEOUT_SECONDS`, exit 1, "no playback_finished for … within 120 s"). Tool tests
    adjusted and added. Hardware checklist step 5 now expects exit 1 on the busy reply.

## 2026-10-31 (hardware, e60e03a)

- Hardware session on production e60e03a, reported by the manager; voice-pe-03 (study), at
  Maciej's request, plus the voice-pe-02 announcement in step 1. Times UTC.
- Step 1: an announcement on voice-pe-03 (16:28:52) and on voice-pe-02 (16:28:58), both
  exit 0; Maciej heard the study (voice-pe-03) one.
- Step 2a: `busy` (idle, capturing speech). Step 2b: `busy` (speaking); the long announcement
  played to the end.
- Step 3: a stop after 4 s ended with one `playback_finished`; "stopped" line at 16:30:38.
- Step 4: an announcement stopped after about 4 s (`--stop-after 4`), exit 0; "stopped" line
  at 16:31:15.
- Step 5: `stop` on an idle satellite gave `busy`, exit 1.
- Step 6: two "stopped" lines, from steps 3 (16:30:38) and 4 (16:31:15), and no "still busy".
- Result: **PASS**.
- Observations (details in the T-022 log, same date): voice-pe-03 ended its voice-assistant
  stream once while listening (16:23:27) and recovered by itself; and without a TTY,
  `docker exec` test clients kept running in the hub container after their ssh client was
  killed, as extra API consumers.
