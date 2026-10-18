# T-008: streaming Polish STT (faster-whisper large-v3-turbo)

## Goal

The STT stage of design.md §4.1 step 3 and §4.2: one shared faster-whisper model on the GPU,
one streaming session per speech segment with partials every `stt.partial.interval_seconds` and
a final transcript at segment end, Polish only.

## Scope

- `src/piotr_voice_hub/stt/whisper.py`: copied from piotr_the_ai_server
  `ai_server/speech_to_text/faster_whisper.py` (design.md §6 "Copy") and trimmed:
  - `FasterWhisperSpeechToText(config.stt)`: `start()` loads the model and warms both beam
    sizes on low-level noise; `create_streaming_session(id, max_audio_seconds)`.
  - `FasterWhisperStreamingSttSession`: `send_audio(pcm)`, `receive_text()` → `TextPartial` …
    `TextEnd`, `transcribe_final()` (beam `stt.beam_size`, whole segment), `close()`.
    Partials use beam `stt.partial.beam_size` over the last `window_seconds`, only on new audio;
    repeated or empty partials are not emitted; a partial is dropped if over 2 s of audio
    arrived while it decoded (old default).
  - Decoding exactly as the old project: `language="pl"`, `beam_size`, faster-whisper defaults
    otherwise. No hotwords and no initial_prompt (manager decision, measured echo).
  - Reliability: one model lock (a partial skips its tick while the model is busy, a final
    waits); a timeout on every GPU await; a timeout or model error is fatal (CRITICAL log,
    process exit, Docker restarts); a partial failure also surfaces from `receive_text()`;
    the event queue and the audio buffer are bounded by the segment cap.
- `stt/transcript_preprocessor.py`: the old replacement rules, plus dropping bracketed Whisper
  notes ("(muzyka)") and symbol-only text ("♪"). Logs at DEBUG.
- Dependencies: `faster-whisper==1.2.1`, `av>=15,<16` (T-003 pins), as main dependencies.
- Tests: unit tests with a fake transcriber (no GPU, no model download); an opt-in GPU
  integration test (`PIOTR_VOICE_HUB_GPU_TESTS=1` + CUDA) run on piotr with
  `tools/stt-gpu-test.sh` in the CUDA 12.8 cuDNN image: one stream, then 4 concurrent streams,
  with device peak VRAM.
- `docs/tasks/README.md`: the P0 task list, including the new T-013 wake-test runner.

## Out of scope

- VAD/segmenting (T-007), wake matching (T-009), session wiring (T-010), Docker hub image.
- New config keys (§7 has none for STT bias or offline mode).
- Hallucination filters the old project did not have (blocklists, confidence thresholds).

## Acceptance

- `make check` passes without a GPU; the GPU test passes on piotr; numbers recorded in log.md.
