# T-008: streaming Polish STT (faster-whisper large-v3-turbo)

## Goal

The STT stage of design.md §4.1 step 3 and §4.2: one shared faster-whisper model on the GPU,
one streaming session per speech segment with partials every `stt.partial.interval_seconds` and
a final transcript at segment end, Polish only.

## Scope

- `src/piotr_voice_hub/stt/whisper.py`: copied from piotr_the_ai_server
  `ai_server/speech_to_text/faster_whisper.py` (design.md §6 "Copy") and trimmed:
  - `FasterWhisperSpeechToText(config.stt, hotwords)`: `start()` loads the model and warms both
    beam sizes (the first GPU call is slow); `create_streaming_session(id)`; `close()`.
  - `FasterWhisperStreamingSttSession`: `send_audio(pcm)`, `receive_text()` → `TextPartial` …
    `TextEnd`, `transcribe_final()` (beam `stt.beam_size`, whole segment), `close()`.
    Partials use beam `stt.partial.beam_size` over the last `window_seconds`; repeated or
    empty partials are not emitted; a partial is dropped if over 2 s of audio arrived while
    it decoded (old default).
  - `hotwords` (the configured `wake_phrase`) is passed to the model when given.
- `stt/transcript_preprocessor.py`: copied verbatim (hardware-tuned corrections).
- Dependencies: `faster-whisper==1.2.1`, `av>=15,<16` (T-003 pins).
- Tests: unit tests with a fake transcriber (no GPU, no model download); an opt-in GPU
  integration test (`tests/test_stt_gpu.py`, `PIOTR_VOICE_HUB_GPU_TESTS=1` + CUDA) run on
  piotr with `tools/stt-gpu-test.sh` in the CUDA 12.8 cuDNN image.

## Out of scope

- VAD/segmenting (T-007), wake matching (T-009), session wiring (T-010), Docker hub image.
- New config keys (§7 has none for STT bias or offline mode).

## Acceptance

- `make check` passes without a GPU; the GPU test passes on piotr; numbers recorded in log.md.
