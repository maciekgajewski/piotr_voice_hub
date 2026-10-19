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
