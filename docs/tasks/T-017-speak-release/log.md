# T-017: log

## 2026-10-19

- Branch `t-017-speak-release` from `origin/main` 0fac79f (T-016 merged), worktree
  `~/src/piotr_voice_hub-t017`. Not based on `t-016-followup` (PR #20): nothing here needs
  `TtsUnavailableError`.
- Read design.md §4.1, §6, the README T-017 row and carry-forwards, the session, API,
  driver playback (T-014) and the T-016 client. plan.md written; the decisions in it follow
  design.md and the rules. The one point design.md does not spell out, what a consumer gets
  when an `ack`ed `speak` fails, is decided by the "no stale state" rationale of the busy
  policy: always exactly one `playback_finished`.
- Implemented as planned: `hub/commands.py` (parse, validate, route; `ack`/`error`), the API's
  reader now hands each message to the router and queues the reply for that client only;
  session `speak`/`release`, `SPEAKING`, `playback_finished`; main wires the TTS client into
  the sessions and the router into the API; the playback HTTP close scaled by the rate.
- `_expect` takes an optional bound (the `PlaybackFinished` wait). Its default must be read at
  call time (`None` → `EVENT_TIMEOUT_SECONDS`): a default bound at definition time ignored the
  tests' monkeypatch of the constant (one existing test failed until fixed).
- The reply wait races the reply future, an event reader task and the reply timeout. A reply
  wins → the reader is cancelled (`wait_for_event` is cancel-safe: queue.get). A disconnect
  in the same step as an `ack` is covered by a test that checks only the invariant (one
  `playback_finished`, then `error`, then `idle`), since which side wins depends on timing.
- GPU end to end (`tests/test_speak_gpu.py`, added to `tools/piper-gpu-test.sh`): our own Piper
  as `-p piotr-voice-hub-t017`, image `piotr-voice-hub-piper:t017` (built with
  `docker build -t`, so `latest` was not touched), port 10211 via an override file
  (`--no-build`; command and healthcheck port). Healthy after 50 s. Real API + router +
  session + TTS client + ESPHome driver + playback HTTP, the driver tests' fake aioesphomeapi
  client as the satellite (its media player fetched the URL over loopback), fake STT. No real
  satellite contacted.
  - `transcript_final` → `speak` (52 characters) → `ack` → `satellite` `speaking` →
    `playback_finished` 3.91 s after the `ack` for 3.53 s of 22050 Hz audio (synthesis, the
    0.1 s volume settle, the drain and its 0.2 s grace) → `idle`. Volume 0.6 sent first.
  - All 7 Piper GPU tests passed (T-015 service, T-016 client, T-017 speak).
  - Torn down: `down`, the `:t017` image deleted, 10211 free. The running hub, its Piper,
    and Maciej's containers untouched (not exec'd into, not restarted).
- PR #21 opened; then PR #20 (T-016 follow-up) was merged as 8803766, so the branch was rebased
  onto it. One conflict, the header comment of `tools/piper-gpu-test.sh`; `run_hub` merged
  cleanly (the TTS client from #20's startup wait is the one handed to the sessions; its
  `TtsUnavailableError` is a `TtsError`, so the session's handling covers it).
- After the rebase: `make check` 3× green; the GPU end to end again with our own Piper
  (`-p piotr-voice-hub-t017`, `:t017` image, port 10211): 7 passed, `speak` 3.59 s of audio,
  `ack` → `playback_finished` 3.97 s. Torn down the same way; 10211 free, production untouched.
- 2026-10-19 14:15: PR #20 was merged (review 5571144796, MERGE, no blockers or should-fix).
  The manager asked for its four nits to go into this PR as a separate commit:
  - **N1:** the startup check's worst case is about 137 s, not 120 s. No attempt starts after
    120 s, and one attempt takes up to 17 s (connect 5 + describe send 5 + info 5 + close 2).
    Fixed in the `hub/main.py` comment and docstring, the compose comment and the T-016 plan.
    The CRITICAL line now says `not reachable after <waited> s (<n> attempts; none starts
    after 120 s)`, and the WARNING says `new attempts until 120 s`. A test checks
    120 + 17 = 137 against the client's constants. The T-016 log's "up to 120 s" stays
    (append-only); this entry corrects it.
  - **N2:** `ConnectionResetError`/`BrokenPipeError` while sending is `TtsUnavailableError`
    (`connection reset while sending <type>`), so the startup check retries it. Tests cover
    both errors for `start()` and `synthesize`; any other `OSError` stays a plain `TtsError`.
    The write raises in the tests, since a real reset cannot be timed to hit the write.
  - **N3:** an exception from the consumer's code inside `synthesize` is logged as
    `consumer failed: <type>`, not as stopped early. Tested.
  - **N4:** a test cancels `check_tts` during the retry sleep: `CancelledError` at once, one
    attempt, no CRITICAL line.
- In the same commit: the PR checklist and plan.md no longer mention Home Assistant (the hub
  owns the satellites; there is none here). The playback-end comparison is against when the
  audio audibly stops. The T-017 README row now names PR #21.
- 2026-10-19 14:22, same commit (amended, pushed with force-with-lease on this branch): VRAM
  docs, at the manager's request. Production on piotr (main 8803766) measured Piper at
  2228 MiB after the 949-character warm-up (up from T-003's 1.2 GB peak). With the hub's
  2220 MiB, that is about 4.4 GB. onnxruntime's CUDA arena keeps its peak, so a request near
  the 1000-character cap would reach this anyway; the warm-up only shows it up front.
  Decision: keep the warm-up. design.md §4.3 now gives Piper about 2.2 GB peak (held) and a
  hub total of about 4.4 GB (about 4.9 GB with concurrent partials), leaving about 11 GB for
  Ollama. T-015 plan.md did not state a ~700 MiB steady state; its T-003 reference line gets
  a pointer to the production number.
- 2026-10-19 14:50: `make check` green 3× on piotr (682 passed, 8 skipped). GPU end to end
  with our own Piper (project `piotr-voice-hub-t017f`, image `piotr-voice-hub-piper:t017f`,
  port 10211, fake satellite): 7 passed; `speak` 3.44 s of audio, `ack` → `playback_finished`
  3.82 s (now without a volume). Torn down, image removed, 10211 free, production untouched.
  `python -c "$(cat tools/speak_test.py)" --help` works (the docstring is the description), and a
  refused connect is one stderr line and exit 1.
- 2026-10-19 14:50: manager's decision on the `tts.volume` leftover: for simplicity, the key is
  removed entirely. It is not kept as a checked-but-unused key, and there is no startup WARNING.
  A config that still has `tts.volume` fails like any unknown key (exit 2); a test covers that.
  The only deployed configs, `~/hub-wake.yaml` and `~/hub-wake.full.yaml` on piotr (ours, mode
  600), had their `tts.volume` removed by a script that printed nothing of their contents. It
  checked that the parsed YAML changed only by that key and kept mode 600. Both still load
  with the new code. The running hub reads its config only at startup, so it is not affected.
  In the same commit, docs/tasks/README.md marks T-016 `merged (PR #19)` and T-017
  `merged (PR #21)`.
- 2026-10-19 14:55: PR #22 was merged as 789d6f3 (review 5571801954, MERGE). This is follow-up 2,
  on branch `t-017-followup-2` from origin/main:
  - **S1:** the 30 s cap (`PLAYBACK_PRODUCE_TIMEOUT_SECONDS`) now covers only the TTS reads,
    meaning opening the synthesis and each chunk read. All of them run against one deadline
    (`loop.time()` + 30 s), with `asyncio.timeout_at` around each read in `_tts_read`. The
    satellite commands (`PlaybackBegin`, the chunks, `PlaybackEnd`, the cue) run outside it,
    so the cap can no longer cancel a begin halfway. Before, that sent the timeout cue while
    the protocol state was `PLAYING_AUDIO`, which tripped an assert and crashed the hub (latent).
    Expiry is a private `_ProduceTimeout`, so a `TimeoutError` from anything else is not taken
    for the cap. A test with a slow `PlaybackBegin` covers both cases. At 0.15 s against a
    0.2 s cap, all chunks play with no error. At 0.3 s the begin completes, the next read hits
    the deadline, and what was produced is played. Either way there is one `playback_finished`.
    Decision: one deadline means a slow begin still uses up TTS time. A per-read budget that
    leaves out the send time was not chosen: it would no longer bound the whole speak.
    The T-016 client now logs a capped speak as `consumer failed: _ProduceTimeout`, where
    it used to say `TimeoutError`. The playback_http docstring now names both bounds.
  - **N1:** the empty-playback test has a third case, the cap firing before the first chunk.
    The timeout cue plays, there is one `playback_finished`, and the ERROR line is
    `audio not complete within 0.1 s; no audio: playing the timeout cue`.
  - **N2:** docs/tasks/README.md: T-015 is now `merged (PR #18)`. The other rows checked:
    T-001–T-014 say merged, T-016/T-017 say merged (PR #19/#21), and T-018 onward are planned.
  - **N3:** re-wrapped T-017 plan.md:41 only. The plan's "Bounds" line
    (`PlaybackBegin` → `PlaybackEnd` at most 30 s) is left as the T-017 history; this entry
    supersedes it.
- The 0.3 s slow-begin case fails against origin/main's `session.py`, so it reproduces
  review S1. With this change, `make check` is green 3× on piotr (683 passed, 8 skipped).
- 2026-10-20 07:05: **Bug from Maciej's T-017 hardware test (wake word, then a pause).** On
  voice-pe-03, 2026-10-20 04:53–04:55Z (06:53–06:55 Warsaw), he said "Ryszardzie, która
  godzina?" with a natural pause after the name, and nothing was accepted (4 times). Fix on
  branch `t-017-split-wake` from origin/main d69ea7b.
  - **Evidence (production, read-only `docker logs`, INFO level).** The logs have no
    transcripts: the hub logs at INFO, and nothing logs the text. The segment lines come in
    pairs, e.g. 04:53:53.595 `utterance-…-365 audio_ms=2176 candidate=True wake=False`, then
    04:53:55.356 `-366 audio_ms=1792 candidate=False wake=False`. The other pairs are
    -367/-368, -370/-371 and -375/-376. The one `device ended its voice-assistant stream
    while listening` (04:54:32Z) re-armed in 1 s and is unrelated.
  - **Cause (from the code, reproduced by a test).** `_finish_segment` accepted only when
    `match_wake_phrase(final)` matched *and* `match.remainder` was non-empty, and
    `wake=` logged exactly that. A final of "Ryszardzie." / "Ryszardzie," has the match with
    an empty remainder (`_remainder` strips the punctuation; `test_phrase_alone_gives_empty_
    remainder`). So the candidate was cancelled: `wake_cancelled`, reset, `IDLE`. The next
    segment ("Która godzina?") has no wake word, so it was dropped as unrelated speech. The
    new test of the wake word alone, then the request, fails on main's `session.py` with
    `WakeCancelled` where `TranscriptFinal` is expected. It is not a punctuation-only problem
    in the matcher: the matcher is right; the session had no notion of a request in a later
    segment.
  - **Old project (read-only, ~/src/piotr_the_ai_server at a299643).** The same rule, with no
    follow-on window. In `ai_server/microphones/manager.py:319-342`, an empty text after the
    phrase (`_text_after_wake_phrase`, `:1155-1159`) is "open-mic speech discarded …;
    continuing stream". That sends `ResetWakeCandidate`, sets `IDLE` and waits for the next
    speech start, which needs the wake word again. design.md already called our rule
    "today's `_capture_open_mic_utterance` logic". Its only grace period after a wake is
    `initial_silence_seconds` = 3 s (`ai_server/config.py:30`, `config.example.yaml:122`).
    That is how long speech may take to start after the *device* wake word (not used in
    open-mic mode, `box3_esphome.py:1012-1013`).
  - **VAD end-of-speech silence.** Ours: Silero, end after 0.8 s with p < 0.35
    (`end_silence_seconds: 0.8`, design §4.1 step 2; start after 250 ms with p ≥ 0.5; 1 s
    pre-roll). Old: amplitude peak below 500 for `end_silence_seconds` = 0.9 s
    (`config.py:31`, `box3_esphome.py:1076`). A 0.1 s difference is not "clearly too short",
    and a natural pause after a name is often longer than either. Not changed; the window
    handles the pause whatever the VAD does.
  - **Fix (smallest change in the session).** A segment that is the wake word alone keeps
    the wake open (`wake` sent, `LISTENING` stays) and keeps listening. If the next segment
    starts within `WAKE_PAUSE_SECONDS` = 3 s (the old 3 s grace above), its whole text is
    the request (`wake.request_text`: edges stripped like a remainder). `transcript_final`
    carries the wake's `utterance_id`, `raw_text` = both finals and `audio_ms` = both
    segments, and the request's partials come with the wake's id.
    - **No request:** if no segment starts in time, or the next one has no letter or digit,
      the hub sends `wake_cancelled` and `ResetWakeCandidate`, then
      `StopListening(no_request)`, the timeout cue and a re-arm.
    - **Exactly one end per wake:** a disconnect in the window goes through `_recover`
      (one `wake_cancelled`, the pending state cleared). A second "Ryszardzie" in the window
      keeps the same wake and restarts the window.
    - **Decision:** the window is a module constant like the other protocol bounds, not a
      config key, so the deployed config still loads.
  - **Tests.** Each case checks `wake` → exactly one `transcript_final`/`wake_cancelled`.
    - The wake word alone, then the request: 3 cases (a partial candidate, the final alone,
      and the wake word repeated in the request). The LEDs stay `LISTENING`, there is no
      reset and no second `wake`.
    - The wake word alone, then silence: `wake_cancelled`, reset, stop, timeout cue, re-arm.
    - The next segment is empty, "♪" or "...": the same.
    - A disconnect in the window: one `wake_cancelled`, no cue, and the next segment
      without a wake word is not taken as a request.
    - One breath is unchanged (the existing tests).
    - The old `test_wake_word_with_nothing_after_it_is_rejected` encoded the bug and is
      replaced.
  - **PR #23 review nits, in the same PR.**
    - **N1:** the TTS chunk generator is entered as `contextlib.aclosing(aiter(speech))` on
      the speak's exit stack, so it closes before the synthesis, also on a satellite failure.
      `Speech.__aiter__` is now typed `AsyncGenerator[bytes, None]` (it always was one).
    - **N2:** T-017 plan.md's Bounds line now describes the one 30 s deadline on the TTS
      reads.
- 2026-10-20 07:16: PR #24 review 5581004177 (CHANGES NEEDED) is fixed with a new commit on
  `t-017-split-wake`. Before the fix, the new B1, S1, N1 and N2 session tests fail on the
  previous head (fe4734a).
  - **B1:** the window opens only when the final is the vocative alone. The new helper
    `wake.is_wake_word_alone` checks that every word normalizes to `ryszardzie`, so
    punctuation, whitespace and repeats are allowed. A sentence that ends in the vocative
    ("Dziękuję, Ryszardzie.", "Dobranoc, Ryszardzie!") takes the old cancel path: wake_cancelled,
    reset, `IDLE`, keep listening, no cue. Tests: a session test ("Dziękuję, Ryszardzie."
    followed by "No to idziemy do kina." gives no `transcript_final`) and helper cases in
    test_wake.py.
  - **S1:** the window now starts when `SpeechEnded` is read, before the final is taken.
    `_BareWake.deadline` = that loop time + 3 s, and the wait uses `asyncio.timeout_at`.
    If a slow final already used the window, the wait ends at the first point where the read
    would have to wait; a `SpeechStarted` already queued is still read. Test: a 0.3 s final,
    a 0.1 s window, and speech pushed 0.35 s after the end. It is cancelled now, and the
    previous head accepted it.
  - **S2:** the tests the reviewer listed are added:
    - the sentence-final vocative;
    - a request that starts in the window and ends after it (one `transcript_final`);
    - the wake word said again (one wake, `u1`, all three segments in `raw_text`/`audio_ms`);
    - a disconnect inside the request segment (one `wake_cancelled`, every STT session
      closed, no cue);
    - S1's late start;
    - a request with a partial and an empty final (the partial under the wake's id, then
      exactly one `wake_cancelled`).
    The TV case (a bare "Ryszardzie!" followed by dialogue within 3 s becomes a request) is
    now a known limitation in design.md §4.1 step 4 and in the PR's hardware checklist.
  - **N1 (manager's decision):** the code now keeps the whole request, as design.md says,
    because that is the simpler of the two options. `wake.request_text` drops only a leading
    wake word (and its repeats). One function serves the final and the partials, and the
    session no longer branches on the match there. "Powiedz, Ryszardzie, która godzina" stays
    whole (tested).
  - **N2:** the wake word said again in the window now extends the open `_BareWake`:
    `raw_text` and `audio_bytes` add the new segment, and the deadline is the new end + 3 s.
  - **N3:** re-wrapped the whole Bounds bullet in T-017 plan.md (line 48 was 134 characters).
- 2026-10-20 07:10: T-017 fix, branch `t-017-cue-race` from origin/main d69ea7b. Bug: after
  deploying 789d6f3, `speak` on voice-pe-03 played nothing and the satellite went unavailable.
  Diagnosis (device logs, hub stopped): the accepted cue is fire-and-forget (no
  `supports_response` yet, P2), so `CueFinished` followed the call and the playback URL went
  out about 0.09 s later, while the cue was still `ANNOUNCING`. The Voice PE silently drops
  that URL: no fetch, no device log, then the 5 s fetch timeout. A direct URL with no cue was
  fetched in 0.1 s; the same session replay with a 0.5 s gap played.
  - The driver now subscribes to entity states and follows the media player. A fire-and-forget
    cue ends once the player has reported an update after the call and is not busy:
    `ANNOUNCING` or `PLAYING` (the API reported the device's announcement as `PLAYING`).
    Bounds: `CUE_START_TIMEOUT_SECONDS` 1 s (a cue that never shows finishes) and
    `CUE_END_TIMEOUT_SECONDS` 8 s; 1 + 8 s stays under the session's 10 s wait for
    `CueFinished`.
  - Deviation from the brief ("ANNOUNCING and back to IDLE"): the first live run waited for an
    `ANNOUNCING` update, and the device never sent one. The cue's `ANNOUNCING` lasted about
    0.1 s on the device log and reached the API merged into a single `IDLE` update, so every
    cue waited out the 1 s start bound with a WARNING (playback still worked). Any update after
    the call now counts; the state wait then covers a longer cue that is still announcing.
  - `_begin_playback` sends the URL only once the player is not busy, at most
    `PLAYER_FREE_TIMEOUT_SECONDS` 3 s.
  - Decision: every bound logs a WARNING and goes on, it does not make the satellite
    unavailable. design.md has no rule for it; a late cue is not worth a reconnect, and a URL
    the device still drops ends in the existing 5 s fetch timeout, which does go unavailable.
    A disconnect during a wait is unavailable at once, as before.
  - Not changed: `session.py`. Firmware P2 (`api.respond`) still gives the exact end where
    declared; the state wait covers the firmware as flashed.
  - The plan's manual test, the T-022 row and the PR checklist get a real-device playback check.
  - Live check on voice-pe-03 (production hub stopped about 10 s per run, then restored on
    789d6f3): the session replay (`StartListening`, accepted stop, accepted cue, `PROCESSING`,
    0.09 s gap, a 0.6 s quiet tone) through this branch's driver. The cue's update came 0.10 s
    after its `ANNOUNCING` started, `CueFinished` followed, the URL went out 0.09 s later, the
    device fetched it in about 0.11 s, and `PlaybackFinished` came with no WARNING. The API
    reported the playback as `PLAYING`, so busy is `ANNOUNCING` or `PLAYING`. On origin/main
    the same replay sent the URL during the cue, and there was no fetch.
  - voice-pe-03's volume: the diagnosis left it at 0.3 (set by the probe); the previous value
    was not read, so it is not restored (for Maciej).
- 2026-10-20 07:28: PR #25 rebased onto origin/main 133caac (PR #24 merged, review
  5581062362, MERGE). The only conflict was this log; both appends are kept, in time order. The
  fix commit's author is now the box identity (Piotr Developer (Grok Bot)), as on earlier PR
  branches; it had been committed as Maciej by mistake. The #24 re-review nits are a separate
  commit, in `session.py` and its tests:
  - **N1 (behaviour kept):** a `SpeechStarted` read after the pause deadline (queued during a
    final slower than the window; the real driver returns a queued event without yielding)
    is still the request, and the session now logs it at DEBUG with how late it was read. A
    new test covers it with `FakeDriver.eager`, which returns queued events without yielding,
    like the ESPHome driver.
  - **N2:** the S1 test pushes `u2` from a delayed task (0.2 s, during the 0.3 s final and
    past the 0.1 s window) and asserts that the push happened while still listening, then
    `wake_cancelled`. With the push no longer conditional, the fake driver needed
    MP-STOP-002 (drop queued capture events on `StopListening`, as the real driver does);
    without it, the queued start reached the protocol checker after the stop.
- 2026-10-20 07:39: PR #25 review 5581142066 (MERGE; the manager holds the merge for S1),
  fixed with new commits on `t-017-cue-race` (no force push):
  - **S1:** any media player update after the cue call counted as the cue's, and a merged
    cue reports only IDLE, so a volume or dial change, a stray IDLE or the late IDLE of the
    previous announcement could end the cue at once while the player still read IDLE, and
    bring the race back. New `CUE_MIN_SECONDS` = 0.3: after the update, the cue waits until
    0.3 s after the call (the measured cue update is 40-200 ms; a 0.5 s gap played), and then
    still needs the player not busy (`ANNOUNCING` or `PLAYING`). The floor waits on the player
    event, so a disconnect still ends it at once. A test sends an extra IDLE 5 ms after the
    call: `CueFinished` comes no earlier than 0.3 s with a short cue, and no earlier than the
    cue's own IDLE (0.5 s) with a longer one; without the floor it fails.
  - **S2:** three new tests: a disconnect during the cue wait (unavailable in under 0.2 s,
    no `CueFinished`); a reconnect, where the old client's updates and the new connection's
    first state are not counted (the cue waits out the start bound with its WARNING); and
    the hardware sequence end to end (a merged IDLE about 0.1 s after the call, then the URL,
    the fetch, PLAYING, IDLE, `PlaybackFinished`, no WARNING). `FakeClient` gained
    `cue_start_seconds`, `extra_update_after`, `url_states` and a `timeline`.
  - **N1:** the earlier "1 + 8 s stays below the session's 10 s wait" (code, design.md, PR
    text, and this log's 07:10 entry) was wrong: the driver emits `CueFinished` before the
    command returns, and the session's 10 s wait starts after that. The bound is the
    driver's own: the call (at most 5 s) + 1 s + 8 s, while the session holds its command
    lock. The comments, design.md §4.1 step 5 and the PR text now say so.
  - **N2:** the `_wait_cue_played` docstring says not busy (ANNOUNCING or PLAYING); the
    module docstring's Cues paragraph is reflowed.
  - **N3:** the DEBUG line is `media player <state> volume=<v> update=<n>`.
  - **N4:** design.md §4.1 step 5 and the constants state the worst case. A player stuck in
    `PLAYING` makes every cue take about 9 s and delays every speak by 3 s, each with a
    `still busy` WARNING, and a URL the device then drops ends in the 5 s fetch timeout. A
    repeating `still busy` is a device problem.
  - **Live re-check** (voice-pe-03, production stopped about 10 s and restored on 789d6f3,
    healthy, both idle). The probe now reads the device volume and does not set it; it
    restores the exact value if it changed. Before: 0.3, after: 0.3. The cue's IDLE update
    came about 0.2 s after the call, `CueFinished` at 0.30 s (the floor), the URL 0.09 s
    later, fetched within about 0.05 s, then PLAYING, IDLE and `PlaybackFinished`, with no
    WARNING. The new DEBUG line showed `volume=0.30 update=1..3`.
- 2026-10-30: follow-up to PR #25 re-review 5581208755 (both nits, MERGE), branch
  `t-017-cue-nits` from main e90f02f:
  - **N1:** the `esphome.py` comments said the cue's report comes 40-150 ms after the call;
    the hardware measured about 40-200 ms (voice-pe-03), only 0.1 s under the 0.3 s floor.
    The comments now say so, and `CUE_MIN_SECONDS` is 0.5 s, the gap known to play (about
    0.2 s more per cue). design.md §4.1 step 5 and the tasks README say 0.5 s. The S1 test's
    second case moved from a 0.5 s to a 0.8 s cue, so it still tests the cue's own PLAYING
    past the floor, and the merged-IDLE cue test now expects `CueFinished` from the floor up
    to 0.3 s past it (was under 0.5 s), still short of the 1 s start bound.
  - **N2:** the disconnect-during-the-cue test now asserts `MicrophoneUnavailable` in under
    0.1 s (the disconnect is at 0.1 s, inside the floor), so a floor that sleeps instead of
    waiting on the player event (about 0.4 s) fails clearly.
  - `make check` passes; with the floor mutated to a sleep, the disconnect test fails. No
    live test; the hardware check is for after deploy.
