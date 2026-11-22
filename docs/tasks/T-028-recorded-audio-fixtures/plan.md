# T-028: recorded-audio fixtures

**Status:** in progress. Branch `t-028-recorded-audio-fixtures`.

## Goal

Replayable 16 kHz mono WAV fixtures so VAD, STT and the wake matcher can be regression-tested
without a live household recording session (design.md §10).

## Scope

- One synthesized set at the 16 kHz 16-bit mono PCM the satellites send, covering: phrase +
  command, a question without the wake word, a near-miss, an inflected phrase, silence, noise,
  speech after a long silence, a follow-up without the wake word, a TV/music stand-in, and two
  overlapping speakers.
- Clips are synthesized (Piper `pl_PL-bass-high`, plus generated silence, noise and tones) or
  reused from already-consented repo test data. No live recording, no satellite capture, no
  API client to the production hub. The capture tool from the old project is not imported.
- A pytest runner sends the clips through the existing VAD/segmenter path and the wake matcher.
  CPU tests run in CI. The STT pass is GPU-marked (skipped in CI) and run on piotr in a
  privately tagged container.
- A manifest records text, expected wake, expected transcript, source, generator command and
  seed. `tests/data/README.md` and `tests/data/fixtures/manifest.json` document how to add a
  consented clip.

## Out of scope

- Permanent or always-on capture, any change to production containers, or satellite flashing.
- Replacing T-022 or T-030 live hardware counts; this task is the offline regression set.
- Per-device (Voice PE vs BOX-3) acoustic recapture. That needs Maciej's approval first; see
  the question below.

## Approach

Prefer synthesized Polish clips (or existing consented files) that cover the cases. Store
small WAVs in `tests/data/fixtures/` with `manifest.json`. Regenerate with
`tools/generate_audio_fixtures.sh` (throwaway `piotr-voice-hub-piper:t028` on 127.0.0.1:10228,
never production piper on 10201). The runner is a pytest target; the STT pass is GPU-marked.

## Tests

- The runner fails if a "phrase + command" clip is not accepted, if an inflected, near-miss,
  no-phrase or follow-up clip is accepted, or if silence, noise or TV/music is accepted as a
  wake.
- Two-speaker and TV/music transcripts are soft (the mixer can confuse STT); a wake accept
  is still a hard fail. VAD on TV/music may open a segment because of the speech-like layers.
- No capture tool is added. Adding a consented clip is a manual copy into `tests/data/fixtures/`
  plus a manifest entry; there is no default household output path.

## Manual (hardware) checklist outline

- [ ] `make check` (CPU subset) is green on the committed fixtures.
- [ ] Optional GPU pass on piotr: `tools/fixtures-gpu-test.sh` (image
      `piotr-voice-hub-fixtures-test:t028`, no production ports). Counts go in the task log.
      No standing recording.
- [ ] If a consented recapture is wanted, Maciej approves the phrases and the output path
      first; files are added only with consent.

## Risks

- Live household audio must not land in git. The review looks at the manifest and the
  generator; there is no capture tool and no household default path.
- STT counts will differ between CPU and GPU; the GPU pass pins `large-v3-turbo`, `cuda`,
  `float16`, `beam_size=5`, as in `tests/test_stt_gpu.py`.
- Clips that are too clean will not catch TV-false-accepts; the TV/music stand-in layers
  overlapping non-wake speech on tones and noise. It is not a recording of the household TV.

## Dependencies

- None in P2 (parallel with T-027). Uses T-006, T-008, T-009.
- Useful before T-030, not a hard gate.

## Question for Maciej (not done in this task)

design.md §10 asks for one set per satellite type (Voice PE and ESP32-S3-BOX-3). This PR
ships one synthesized set, because a recapture from the real boxes needs your approval of
the phrases and the output path first. Proposed, if you want it later:

- Phrases: the same ten cases as `tests/data/fixtures/manifest.json` (wake + question,
  question only, "Rysiek, włącz światło", "Ryszarda nie ma w domu", silence, room/TV noise,
  wake after a pause, "Ustaw minutnik na pięć minut", TV or music in the room, two speakers).
- Output path: `~/probe/t028-consented/` on piotr (outside git). Selected clips would be
  copied into `tests/data/fixtures/` only after you agree.
- No recording will be started until you say yes to both the phrases and that path.
