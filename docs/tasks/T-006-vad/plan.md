# T-006: Silero VAD

## Goal

Replace the old peak-loudness gate with a neural VAD (design.md §4.1 step 2): a small wrapper
around Silero VAD on CPU onnxruntime with the fixed hysteresis, ready for the ESPHome driver (T-007).

## Scope

- `src/piotr_voice_hub/satellites/vad.py`:
  - `Hysteresis` (pure): start when p >= 0.5 for 250 ms (8 frames of 32 ms); end after
    `end_silence_seconds` (config, 0.8 s, so 25 frames) with p < 0.35.
  - `load_session()`: one shared CPU `onnxruntime` session (1 thread).
  - `SileroVad`: per-stream recurrent state + 64-sample context + hysteresis; `process_frame()`
    takes exactly one 512-sample (1024-byte) 16 kHz 16-bit mono frame.
- Vendored `silero_vad.onnx` (silero-vad v6.2.3, MIT) + its LICENSE, shipped in the package.
- Dependencies: `onnxruntime` (CPU) and `numpy`. Never `onnxruntime-gpu` (design.md §9).
- Tests: hysteresis edges on synthetic probabilities; model hash; real model on silence/noise and
  on a 2.1 s Polish speech clip; a loose per-frame cost guard; no onnxruntime-gpu in `uv.lock`.

## Out of scope

- Pre-roll buffer, 15 s max segment, re-chunking driver audio into frames, the driver itself (T-007).

## Acceptance

- `make check` passes on piotr and in CI (CPU only). Per-frame CPU time measured on piotr.
