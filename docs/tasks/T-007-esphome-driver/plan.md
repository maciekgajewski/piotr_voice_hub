# T-007: ESPHome satellite driver (capture path)

## Goal

`src/piotr_voice_hub/satellites/esphome.py` (design.md §5, §6 "ESPHome driver: copy, adapt"):
the hub's driver for Voice PE and S3-BOX-3 over the ESPHome native API, in continuous mode,
with Silero VAD segmentation. It implements the `Microphone` interface and passes
`tests/microphone_driver_conformance.py` against a fake ESPHome connection.

## Split (approved by the manager, 2026-10-18; the old driver is 1327 lines plus 1552 of tests)

- **T-007 (this task): connection and capture.** Connect/reconnect with timeouts, transport
  recovery, `StartListening` → `start_open_mic_listening`, `StopListening` → `stop_listening`
  with stop ack, audio → Silero VAD → `SpeechStarted`/`AudioChunk`/`SpeechEnded`, 1 s pre-roll,
  15 s max-segment cut, `ResetWakeCandidate`, visual-state and cue services. Playback commands
  fail loudly (`NotImplementedError`) until T-014. Enough for T-013 (wake-test runner).
- **T-014 (new): ESPHome playback.** The shared playback HTTP server (`/<playback_id>.wav`),
  `media_player_command(media_url, announcement=True)`, drain and `PlaybackFinished`, the
  playback conformance contract. Needed by T-010/T-011 `speak`/`play`, not by T-013.

## Scope (T-007)

- Copy and adapt `ai_server/microphones/drivers/box3_esphome.py` @ a299643: drop the peak
  gate, wake-word and follow-up service paths, `ListeningMode`, initial-silence timeout, and
  playback (T-014). Keep the old fixes: pre-roll bound, stop ack, transport recovery, dropping
  late capture events.
- Bounded event queue; a timeout on every await (connect, service calls, stop ack).
- Carry-forwards:
  1. A conformance check for **MP-STOP-002**: capture events queued before `StopListening`
     are dropped (added to `tests/microphone_driver_conformance.py`, run against the fake
     driver and the ESPHome driver).
  2. VAD `Hysteresis.force_end()`/`reset()` for the 15 s max-segment cut (and on disarm).
  3. `SpeechStarted` fires at least 256 ms after onset (8 × 32 ms frames at p ≥ 0.5), so the
     1 s pre-roll is required; say so in the `vad.py` docstring.
  4. A loud non-speech test clip: no such clip exists yet. `audio/playback/timer_finished.flac`
     (a loud alarm tone) and the firmware cue sounds in the old repo are loud non-speech; use
     one (converted to 16 kHz WAV, provenance in `tests/data/README.md`) for a VAD test that
     no segment opens.
  5. One process-wide id factory for all id types (`src/piotr_voice_hub/ids.py`: per-process
     random prefix plus a counter, like the old `conversations/id_factory.py`), used by the
     driver and later the session; never per-connection counters. Test: ids stay unique across
     a reconnect.
  6. A dated correction entry in the T-005 log: its rebase base was 355c811, not 02a75fe.
  7. STT follow-up from the PR #8 review: a model error in a call already abandoned by
     `close()` must also be fatal (CRITICAL plus exit 70), not only logged at ERROR, since all
     model errors fail loudly. Test with a recording `on_fatal`.
- Tests: a fake `aioesphomeapi` client (callbacks for start/audio/stop, service calls,
  disconnect), conformance suite, recovery, pre-roll, max-segment, stop ack, late events.
- Dependency: `aioesphomeapi` (pinned to what the old project used, if it still installs).
- A manual test checklist for Maciej's hardware acceptance (Voice PE and S3-BOX-3) in the PR.

## Out of scope

- Playback (T-014), session/wake acceptance (T-010), firmware changes (design §8), STT.

## Acceptance

- `make check` passes; conformance checks pass for capture and cues; hardware checklist ready.
