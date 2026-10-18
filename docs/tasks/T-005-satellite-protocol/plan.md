# T-005: satellite protocol

## Goal

Bring the manager/driver satellite protocol into the hub (design.md §5 "Driver messages",
"Protocol state machine", "Interfaces/types"), trimmed to continuous (open-mic) listening, with
its MP-* invariant asserts and conformance tests. No driver, no session yet.

## Scope

- Copy from `piotr_the_ai_server` @ `a299643` into `src/piotr_voice_hub/satellites/`:
  `messages.py`, `protocol.py`, `interfaces.py`, `types.py`. Trim to OPEN_MIC only.
- Copy and trim `docs/microphone-protocol.md`.
- Adapt `tests/test_microphone_protocol.py` (as `tests/test_satellite_protocol.py`) and
  `tests/microphone_driver_conformance.py`; delete wake-word and follow-up mode cases.
- Exercise the reusable conformance checks against a tiny in-memory fake driver, so they are
  tested before the ESPHome driver (T-007) uses them.

## Out of scope

- The ESPHome driver, VAD, session, wake matcher, API, TTS.
- Renaming classes (`Microphone*` names kept as in the source).

## Acceptance

- Protocol tests pass (state/command/event matrices, stale-id rejection, invariants).
- No wake-word or follow-up listening mode remains. `make check` and CI pass.
