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
