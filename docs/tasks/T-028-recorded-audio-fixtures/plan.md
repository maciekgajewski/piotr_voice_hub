# T-028: recorded-audio fixtures

**Status:** planned. Branch `t-028-recorded-audio-fixtures`.

## Goal

Replayable 16 kHz mono WAV fixtures so VAD, STT and the wake matcher can be regression-tested
without a live household recording session (design.md §10).

## Scope

- One set of clips per satellite type (Voice PE and ESP32-S3-BOX-3): phrase + command,
  inflected phrase, no phrase, TV/music, silence, two speakers.
- Clips are synthesized or consented. No permanent recording of the household. If a live
  capture tool is imported from the old project (`capture_voice_samples.py`), it writes only
  to an explicit output directory, is off by default, and is never pointed at a standing
  store of household audio.
- A check that runs the clips through VAD, STT and the matcher and reports false accepts and
  false rejects. Counts can be re-checked on the GPU before a release.
- Document how to add a consented clip and the naming layout.

## Out of scope

- Permanent or always-on capture, any change to production containers, or satellite flashing.
- Replacing T-022 or T-030 live hardware counts; this task is the offline regression set.

## Approach

Prefer synthesized Polish clips (or existing consented files) that cover the six cases.
Import the old capture tool only if a consented live recapture is later needed, and keep it
narrow. Store small WAVs in `tests/data/` (or a dedicated fixtures directory) with a manifest
of expected matcher/STT outcomes. The runner is a pytest target, optionally GPU-marked for
the STT pass.

## Tests

- The runner fails if a "phrase + command" clip is not accepted, if an inflected or
  no-phrase clip is accepted, or if silence/TV is accepted as a wake.
- Two-speaker and TV/music cases have documented expected outcomes (may be softer than a
  hard fail if the model is known to struggle; say so in the manifest).
- The capture tool, if present, refuses to run without an output path and does not default
  to a household directory.

## Manual (hardware) checklist outline

- [ ] `make check` (CPU subset) is green on the committed fixtures.
- [ ] Optional GPU pass on piotr, with a privately tagged image and no production ports,
      records the counts in the task log. No standing recording.
- [ ] If a consented recapture is wanted, Maciej approves the phrases and the output path
      first; files are added only with consent.

## Risks

- Live household audio must not land in git. The review looks at the manifest and the tool
  defaults, not at anyone's private recordings.
- STT counts will differ between CPU and GPU; pin the device and model settings used for the
  recorded numbers.
- Clips that are too clean will not catch TV-false-accepts; include realistic noise.

## Dependencies

- None in P2 (parallel with T-027). Uses T-006, T-008, T-009.
- Useful before T-030, not a hard gate.
