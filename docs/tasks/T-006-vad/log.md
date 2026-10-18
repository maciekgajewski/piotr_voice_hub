# T-006 log

## 2026-10-18

- Branch `t-006-vad` off `origin/main` @ `02a75fe` (T-004 merged).
- Model: silero-vad **v6.2.3** (tag commit `5cd7945676eb32225748052e2e6a0580e4686a08`),
  `src/silero_vad/data/silero_vad.onnx`, 2 327 524 bytes,
  sha256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` (asserted in
  `tests/test_vad.py`). License: MIT (Silero Team), vendored as `silero_vad.LICENSE`. Both are in
  the wheel.
- Input handling follows silero-vad's own `OnnxWrapper`: 512 samples per frame at 16 kHz, with the
  previous frame's last 64 samples prepended, `state` (2, 1, 128) carried per stream, `sr`=16000.
- Hysteresis numbers from design.md §4.1: start p >= 0.5 for 250 ms, so ceil(250/32) = 8 frames
  (256 ms); end after 0.8 s with p < 0.35, so 25 frames. Any frame at or above 0.35 resets the end
  count; any frame below 0.5 resets the start count. The end duration comes from
  `satellites.*.end_silence_seconds` (design.md §7); the thresholds and the start duration are
  fixed constants.
- onnxruntime: plain CPU `onnxruntime` (1.31.0 locked), now a direct dependency. design.md §4.1
  says it is "already a faster-whisper dependency", which is still true when STT lands. §9 says the
  hub image must never add `onnxruntime-gpu`; a test asserts `uv.lock` has none. CI stays CPU-only.
  No design change needed.
- Test clip `tests/data/speech_pl_16k.wav` (67 KB): the T-003 Piper clip (pl_PL-bass-high,
  Apache 2.0), converted to 16 kHz mono with ffmpeg (provenance in `tests/data/README.md`).
- Measured on piotr (Ryzen 5 5600, onnxruntime 1.31.0, 1 thread): **0.104 ms wall median per
  32 ms frame** (p99 0.140 ms, max 0.174 ms over 2000 frames); CPU time (process_time) 0.105 ms per
  frame, about 0.3 % of one core per satellite.
- Real clip (1 s silence + clip + 1.5 s silence): speech starts at frame 42 (1.344 s; the speech
  begins about 1.06 s and one 0.35 frame delays the start run), ends at frame 121 (3.872 s, 0.8 s
  after the last speech frame). Silence and low noise stay below 0.02.
- `make check` passes. Status: pushed, PR to be opened.
