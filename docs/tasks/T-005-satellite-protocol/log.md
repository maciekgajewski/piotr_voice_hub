# T-005 log

## 2026-10-18

- Branch `t-005-satellite-protocol` off `origin/main` @ `b9721bc`.
- Copied from `piotr_the_ai_server` @ `a299643` (read-only) into `src/piotr_voice_hub/satellites/`.

### Removed, and why

- `ListeningMode` enum and the `mode` field of `StartListening` / `ListeningStarted`: the hub has
  exactly one listening mode, continuous open-mic (design.md §1, §4.1 step 1). A one-value enum
  adds nothing. `WAKE_WORD` (on-device wake word) is removed from the design (§8), and the old
  driver-level `FOLLOW_UP` mode is replaced by manager policy on top of continuous listening (§4.1 step 7).
- The protocol's per-mode branch: `SpeechEnded` now always returns to `LISTENING` (the old
  WAKE_WORD/FOLLOW_UP path went to `DISARMED`). The "mismatched mode" check went with the field.
- `SpeechStarted.wake_word`: only the on-device wake-word mode filled it.
- `CueType.FOLLOW_UP_TIMEOUT` renamed to `TIMEOUT`: per design §4.1 steps 5 and 7 and §8, the same
  timeout cue is played for the reply timeout and the follow-up window.
- `TextToSpeech` protocol and `SynthesizedAudio*` messages: design §5 says to drop
  `TextToSpeech.speak()`. The rest belongs to the TTS module in P1, not the satellite protocol.
- `interfaces.py` import of `SpeechToText`/`SttSession` (unused) and `types.py` `__all__`.
- Tests: `test_wake_word_generation_disarms_after_one_segment` and the `FOLLOW_UP`
  "mismatched mode" case. The conformance listening check no longer takes a mode and always ends
  with `StopListening`.
- Doc: wake-word and follow-up mode sections, Conversation/agent/mapping references,
  `stt.log_transcripts` (not in design §7; transcripts are now logged only at DEBUG), the
  wake-word sequence, old implementation/conformance paths, and T-009 compatibility history.
  Added the reply timeout and the hub's follow-up window to the timeout table and visual transitions.

### Kept unchanged

- All MP-* invariant asserts, the full command/event x state matrices, `ResetWakeCandidate`
  (design §8 keeps `reset_open_mic_wake_candidate`), cues, playback messages (P1 `speak`), visual
  states and the `MicrophoneUnavailable` exception.

### Added

- `tests/test_microphone_driver_conformance.py`: runs the reusable conformance checks against a
  minimal in-memory fake driver that validates both directions with `MicrophoneProtocolState`.

- Style only: `TypeAlias` unions became Python 3.12 `type` statements (ruff UP040); imports merged
  per ruff isort.
- `make check` passes (191 tests). Status: pushed, PR to be opened.

## 2026-10-18 (review of `8129395`: changes needed)

Review: https://github.com/maciekgajewski/piotr_voice_hub/pull/5#pullrequestreview-5564362498.
Rebased onto `02a75fe` (T-004 merged). Fixed:
- **Blocker:** `MicrophoneProtocolState._used_ids` grew forever. Under continuous listening every
  VAD segment claims an id, which on a healthy connection meant unbounded memory over weeks. Reuse
  is now checked against the `RECENT_ID_WINDOW` = 1024 most recent ids (a deque plus a set; the
  oldest is evicted). Process-wide uniqueness stays the id factory's job (monotonic ids).
  `ResetWakeCandidate` only needs recent utterances, so it still works. New test: 5120 segment
  cycles stay at <= 1024 tracked ids, a recent reuse still asserts, an evicted id is unknown to
  `ResetWakeCandidate`. Documented in docs/microphone-protocol.md (MP-ID).
- The matrix tests now also assert the next state of every legal command/event.
- Reconnect tests: a failure during capture and during playback, then re-arm (or replay) with a
  new id. Reusing the old id and old-generation events are rejected.
- docs/microphone-protocol.md: new MP-STOP-002. After `StopListening` the driver drops queued or
  late capture events of the stopped generation.
- design.md §4.1 step 7: the follow-up ready cue plays while disarmed and listening re-arms after
  it; on follow-up timeout the hub stops listening, plays the timeout cue, sets IDLE and re-arms.
- Skipped as agreed: renaming the `MicrophoneUnavailable` exception/event pair (nit 6), and the
  API-event cross-references in the doc (nit 7).

### Carry-forwards

- **T-007 (ESPHome driver) plan:** add a conformance check that capture events queued after
  `StopListening` are dropped (MP-STOP-002), the way the source driver drains them
  (`box3_esphome.py` around line 855).
- **T-010 (session) plan:** the session must finish the final transcript and send any
  `ResetWakeCandidate` before it consumes the next driver event. `ResetWakeCandidate` is legal
  only in `LISTENING`, and back-to-back segments are common in continuous mode.
- **P1:** `PlaybackBegin/Chunk/End` stream raw PCM, but design §4.1 step 6 / §6 `play` hands the
  satellite a URL (shared playback HTTP server, `media_player` announce). P1 needs a URL playback
  command or a rework of these messages.
- **T-007 (ESPHome driver) plan, from the PR #6 (T-006) review:**
  (a) VAD `Hysteresis` needs a `force_end()`/`reset()` for the 15 s max-segment cut;
  (b) `SPEECH_STARTED` fires at least 256 ms after speech onset (8 frames), so the 1 s pre-roll
  is required, and the `vad.py` docstring should say so;
  (c) add a loud non-speech test clip (TV/music/clatter) if one is available.
