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
