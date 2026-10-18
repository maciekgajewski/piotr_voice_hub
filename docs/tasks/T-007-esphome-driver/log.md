# T-007 log

## 2026-10-18

- Branch `t-007-esphome-driver` off `origin/main` @ `c3a8dd6` (T-005 merged).
- Read the old driver @ a299643 (read-only, `git show`): `drivers/box3_esphome.py` is 1327 lines
  and covers connection/recovery, capture with the peak gate, cues/visuals, and playback (an
  HTTP WAV stream per playback plus `media_player` announce and drain, about 400 lines).
  Its tests are 1552 lines. Proposed split: T-007 connection and capture, a new T-014 for
  ESPHome playback. See plan.md. Implementation waits for the split decision.
- Loud non-speech clip: none in this repo. Candidates in the old repo:
  `audio/playback/timer_finished.flac` and `firmware/esphome/sounds/*.wav`.

## 2026-10-18: implementation

- The split was approved by the manager. Rebased onto `origin/main` @ `60e2caf` (T-008 merged),
  with the `aioesphomeapi==45.3.1` dependency added again on top of the new lock. The old
  project has no `aioesphomeapi` pin in this tree; 45.3.1 is the version T-007 started with
  and is the current release. Its API matches what the old driver used (`connect(on_stop,
  login)`, `subscribe_voice_assistant(handle_start, handle_stop, handle_audio(data, data2))`,
  `list_entities_services`, `execute_service`, `send_voice_assistant_event`).
- `satellites/esphome.py` (`EsphomeSatellite`), about 450 lines against 1327 in the old driver:
  - Connects lazily with a 10 s timeout, lists services once per connection and subscribes to
    the voice assistant. Every await on the device has a timeout (connect 10 s, service 5 s,
    disconnect 5 s with a forced fallback, stop ack 2 s).
  - Failures (transport stop, connect, service error or timeout, missing stop ack, a full event
    queue, or the device ending its stream while armed) make the driver unavailable:
    queued events are dropped and `wait_for_event()`/`send_output_event()` raise
    `MicrophoneUnavailable` until the session calls `close()`. The next call reconnects. This is
    the old driver's recovery boundary, kept simple.
  - The device ending its stream while we are armed is new as a failure. The old driver ended
    the segment and then listened to nothing, since idle open-mic `LISTENING` has no liveness
    timeout (MP-TIMEOUT-001). Now it fails loudly and the session re-arms.
  - Segmentation: device chunks are re-framed to 32 ms for Silero. Before a segment, frames go
    into a 1 s ring (31 frames). On `SPEECH_STARTED` the driver emits `SpeechStarted` and then one
    `AudioChunk` with the pre-roll, which holds the onset. During a segment each frame is one
    `AudioChunk`. The segment ends on `SPEECH_ENDED` ("completed") or at `max_segment_seconds`
    ("max_length", `Vad.force_end()`). No `AudioProgress`: chunks every 32 ms already show
    liveness.
  - `StopListening` disarms first, drops queued capture events (MP-STOP-002), calls
    `stop_listening`, waits for the device's stop callback (MP-STOP-001), then sends
    `VOICE_ASSISTANT_RUN_END`. A missing ack drops the transport and raises.
  - A device start or audio while disarmed is ignored (as in the old driver).
  - The event queue is bounded at 2048. Overflow means the reader is stuck, so the driver fails
    loudly rather than drop audio silently.
  - Cues: `UTTERANCE_ACCEPTED` → `play_message_end_cue`, `TIMEOUT` →
    `play_conversation_timeout_cue`, `FOLLOW_UP_READY` → `play_follow_up_ready_cue` (new hub
    firmware service, design §8; on the old firmware it fails loudly as a missing service). As
    in the old driver, `CueFinished` follows the service call: `execute_service` doesn't wait
    for the script, so the cue may still be playing. Revisit with the firmware (P2) or T-014.
  - A missing required service raises `RuntimeError`. A missing
    `reset_open_mic_wake_candidate` only warns (optional in the old driver too).
  - Playback commands raise `NotImplementedError` (T-014).
  - Kept from the old driver: the narrow race where `StopListening` arrives before the device's
    start callback (the run is not active yet, so no stop is sent). It's rare. A start that
    arrives while disarmed is ignored and its audio dropped.
- `ids.py`: `new_id(kind)` gives `kind-<12 hex per-process prefix>-<counter>`, one counter for
  all kinds. The driver uses it for utterance ids. Tests: uniqueness and one shared counter. The
  driver test checks that ids stay unique across a reconnect.
- VAD: `Hysteresis.force_end()`/`reset()` and `SileroVad.force_end()`/`reset()` (`reset` also
  clears the recurrent state, used on arm and on each new device stream). The `vad.py`
  docstring explains why the 1 s pre-roll is needed (the start fires at least 256 ms late).
- Loud non-speech clip: `tests/data/alarm_tone_16k.wav` from the old repo's
  `audio/playback/timer_finished.flac`. Peak 32758 (it would trip the old peak gate), Silero max
  p 0.032, no segment. Tested in `test_vad.py` and through the driver.
- Conformance: the capture contract now accepts any `AudioChunk` split and checks that the
  speech is contained in the captured audio, since a VAD driver adds pre-roll and trailing
  silence. A new MP-STOP-002 contract: queued and late capture events are dropped, and
  `ListeningStopped` and then a cue's `CueFinished` are the next events. The fake driver and
  the ESPHome driver (fake client, and once with real Silero on `speech_pl_16k.wav`) pass the
  capture, stop and cue contracts. The ESPHome tests also validate every command and event with
  `MicrophoneProtocolState`.
- STT follow-up (PR #8 review): when a model call fails after `close()` abandoned it, the
  error is now passed to `on_fatal` (CRITICAL plus exit 70), not only logged at ERROR. Test
  `test_model_error_after_close_is_still_fatal`; it fails on the old code.
- `make check`: ruff and pyright clean, 370 passed, 1 skipped (the GPU test).
