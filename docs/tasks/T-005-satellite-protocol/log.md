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
