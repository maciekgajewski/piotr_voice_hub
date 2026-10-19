# T-010: session state machine

## Goal

`src/piotr_voice_hub/hub/session.py` (design.md §4.1 steps 1–5, §5 "Session: rewrite", §9
reliability): one `SatelliteSession` per satellite that owns its driver, runs every segment
through STT and the wake matcher, and emits hub events. T-011 puts the events on the WebSocket
API, T-012 wires and supervises the sessions.

P0 only (README): arm, wake candidate, accept, cancel, reply timeout. No follow-up, no busy
policy, no `speak`/`play`/`stop`/`release` (P1), no speaker recognition (P1: `speaker` is
`null`), no playback (T-014).

## Behaviour

- **Start:** `run()` first emits `satellite(connected=false, state=error)`, so the error
  state is shown before the first connect.
- **Arm** (§4.1.1): `SetVisualState(IDLE)`, `StartListening(new_id("listen"))`, expect
  `ListeningStarted`. Emit `satellite(connected=true, state=idle)`.
- **Segment:** `SpeechStarted` opens an STT session (`max_segment_seconds` + 0.1 s, the driver
  ends a segment on a whole 32 ms frame) and a task reading its partials; `AudioChunk` feeds
  it; `SpeechEnded` takes the final. The session owns the segment (`_segment`) until its STT
  session is closed and its partial task has ended, on the normal path and when it is
  abandoned (cancellation, failure); the partial task is cancelled before anything else is
  awaited, so it can never send or emit after `run()` stopped (PR #12 review B1).
- A crash in the partial task is noticed at the next end of speech (when the final awaits the
  task), or when the segment is abandoned.
- **Partial match** (§4.1.4): on the first partial with the wake word emit `wake`, set the
  `LISTENING` visual, `satellite(state=listening)`. Each later matching partial with a
  non-empty remainder emits `transcript_partial` (post-phrase text, §6).
- **Final:**
  - Wake word confirmed and remainder non-empty: **accept** (§4.1.5). If no partial matched,
    `wake` is emitted first, so every `transcript_final` follows a `wake`. Then
    `StopListening(reason="accepted")` → `ListeningStopped`, `PlayCue(UTTERANCE_ACCEPTED)` →
    `CueFinished`, `SetVisualState(PROCESSING)`, `satellite(state=processing)`, and
    `transcript_final {utterance_id, text: remainder, raw_text, follow_up: false,
    speaker: null, audio_ms}`.
  - Otherwise, if a partial had raised a candidate: `wake_cancelled`, `ResetWakeCandidate`,
    `SetVisualState(IDLE)`, `satellite(state=idle)`. Without a candidate nothing is emitted.
  - If the satellite fails after `wake` but before `transcript_final`, recovery emits
    `wake_cancelled`, so every `wake` ends with exactly one of the two (design.md §6).
- **Reply timeout:** after an accept the satellite stays `PROCESSING` for
  `reply_timeout_seconds` (30 s). In P0 nothing can end it early (`release`/`speak` are P1).
  Then `PlayCue(TIMEOUT)` → `CueFinished` and re-arm. The wait reads driver events, so a
  disconnect during it is noticed at once; any other event there is an invariant failure.
- **Events** are frozen dataclasses in `hub/events.py`, handed to a synchronous `emit`
  callback. `ts` and the envelope are T-011's. State values follow §6: `idle` (armed, no
  candidate), `listening` (wake candidate), `processing` (waiting for a reply), `error`
  (offline). `speaking` comes with P1 playback.

## Carry-forwards (docs/tasks/README.md)

1. **Strict ordering per satellite:** one loop reads the driver's events in order and takes
   the final of segment N (and awaits its partial task) before it reads segment N+1's
   `SpeechStarted`, so every event of segment N is emitted before any of N+1. Test: N+1
   queued while N's final is still running.
2. **Commands serialized:** the session is the driver's only command sender; one lock per
   session, held across `send_output_event` (the partial task sends the `LISTENING` visual).
   Tests use a fake driver that records any overlap and assert there is none.
3. **Re-arm backoff:** on `MicrophoneUnavailable` close the driver, emit
   `satellite(connected=false, state=error)`, log one WARNING line per attempt, sleep
   1 s doubling to 30 s, reset after a successful arm, re-arm.
4. **No follow-up and no busy handling** (P1).
5. **Driver: pending start/stop waits fail fast on disconnect.** `_fail()` also sets
   `_stream_started` and `_stream_stopped`; after each wait the driver checks `_unavailable`
   and raises `MicrophoneUnavailable` with the real reason instead of a timeout 8 s / 2 s later.
6. **Driver: half-open connection cleaned up on cancel.** README files it with item 5 as a
   driver follow-up "before T-010", so it is done here: if `_connect` is cancelled after
   `client.connect()` but before the client is published, the client is disconnected before
   the cancellation propagates.
7. **ids:** listen and cue ids come from `ids.new_id`; utterance ids are the driver's (also
   `new_id`). Nothing else creates ids.

## Reliability rules (§9)

- Every command and event goes through a `MicrophoneProtocolState` (fresh one after each
  `close()`); a violation is an `AssertionError`.
- `AssertionError` and any other non-`MicrophoneUnavailable` exception propagate out of
  `run()` (fail loudly; T-012 exits the process so Docker restarts it, §9 "process exit on
  invariant failure"). This differs on purpose from the throwaway T-013 runner, which
  isolates per-satellite errors.
- Every wait for an expected driver event (`ListeningStarted`, `ListeningStopped`,
  `CueFinished`) has a 10 s timeout, treated as `MicrophoneUnavailable` (recover). Waiting for
  speech while armed has no timeout (the audio-liveness check is P2).
- STT failures already exit the process (T-008, exit 70).
- On cancellation the open segment is abandoned and nothing more is sent or emitted.
  `shutdown()`, called by T-012's main after cancelling `run()`, is the clean stop: if armed,
  `StopListening(reason="shutdown")`, then `SetVisualState(IDLE)`, best effort within 3 s,
  then `close()`. An offline satellite is only closed. It emits no events.
- Logging: one WARNING per failed (re-)arm attempt, from the session; the driver logs a
  failed connect at INFO.

## Reused from T-013 (not imported)

The per-segment loop, the partial-reader task, the per-satellite command lock, the backoff
rule, and the fake microphone/STT test doubles.

## Tests

`tests/test_session.py` with a fake driver (scripted events, records commands and overlap),
a fake STT and short real timeouts:

- partial wake → accept → `transcript_final` fields, command order, reply timeout → timeout
  cue → re-arm with a new listen id;
- accept on the final only (`wake` emitted first);
- partial candidate rejected by the final; wake word with an empty remainder;
- no wake: no events; strict ordering across two queued segments;
- `MicrophoneUnavailable` on arm and while listening: backoff 1, 2, reset, capped at 30,
  `satellite` offline/online events, one log line per attempt;
- disconnect during the reply wait recovers at once;
- missing `ListeningStarted` / `CueFinished` within the timeout recovers;
- an invariant failure (an event out of order) propagates;
- ids are `new_id` ids and unique.

Driver (`tests/test_esphome_driver.py`): disconnect during the start wait and during the
stop-ack wait fails fast with the transport reason; cancelling a reader mid-connect
disconnects the half-open client.

## Manual hardware test

Not hardware-testable until T-012 (main) wires sessions to real drivers, and T-011 exposes the
events. The live wake path is already testable with the T-013 runner. The PR checklist says so.
