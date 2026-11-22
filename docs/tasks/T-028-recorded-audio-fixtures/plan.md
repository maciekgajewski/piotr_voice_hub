# T-028: recorded-audio fixtures

**Status:** merged (PR #40, 362ffe0). Branch `t-028-recorded-audio-fixtures`.

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
  seed. `tests/data/README.md` and `tests/data/fixtures/manifest.json` document the
  synthesized set.

## Out of scope

- Permanent or always-on capture, any change to production containers, or satellite flashing.
- Replacing T-022 or T-030 live hardware counts; this task is the offline regression set.
- Per-device (Voice PE vs BOX-3) acoustic recapture into git. Maciej decided (2026-11-22)
  that the real-box recapture is off: the repo is public, so his voice must never be
  committed. If real clips are ever wanted, they stay on piotr outside git and need his
  explicit yes.

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
- No capture tool is added. Real clips are not copied into `tests/data/fixtures/` or
  committed; they stay on piotr outside git if they are ever wanted.

## Manual (hardware) checklist outline

- [ ] `make check` (CPU subset) is green on the committed fixtures.
- [ ] Optional GPU pass on piotr: `tools/fixtures-gpu-test.sh` (image
      `piotr-voice-hub-fixtures-test:t028`, no production ports). Counts go in the task log.
      No standing recording.
- [ ] No recapture into git. If real clips are ever wanted, they stay on piotr outside git
      and need Maciej's explicit yes; there is no recording in a box session.

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

## Decision (Maciej, 2026-11-22)

The real-box voice recapture is off. The repo is public, so Maciej's voice must never go
into it. The per-satellite fixture set is synthesized only. If real clips are ever wanted,
they stay on piotr outside git and need Maciej's explicit yes. There is no recording in a
box session.
