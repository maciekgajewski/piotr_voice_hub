# T-008 log

## 2026-10-18

- Branch `t-008-stt` off `origin/main` @ `c3a8dd6` (T-005 merged).
- Copied the old `faster_whisper.py` and trimmed it to the streaming session. Dropped: the
  non-streaming `SttSession`, `language` (fixed `pl`), `local_files_only` and
  `log_transcripts` config (not in design.md §7: the model downloads into the HF cache on first
  start, and transcript text is logged only at DEBUG), the temp WAV per call (the audio goes to
  the model as a float32 array, so no ffmpeg/av decode per partial), and the extra partial after
  audio end (the final replaces it; `transcribe_final()` no longer waits up to one interval).
- Bug found and fixed while testing: `close()` before the partial worker ever ran left
  `receive_text()` waiting forever (the cancelled task never reached its `finally`). `TextEnd` is
  now sent exactly once, by the worker or by `close()`.
- `make check` (no GPU): 268 passed, 2 skipped (the GPU test is opt-in). Gating on a CUDA device
  alone was not enough: piotr's host has the driver but no cuBLAS, so the test ran, downloaded
  the model and failed with `libcublas.so.12`. It now also needs `PIOTR_VOICE_HUB_GPU_TESTS=1`,
  which `tools/stt-gpu-test.sh` sets.
- Test clip `tests/data/wake_pl_16k.wav` (2.6 s): "Ryszardzie, jaka będzie jutro pogoda?",
  synthesized by the running `piotr-wyoming-piper` (pl_PL-bass-high), resampled to 16 kHz.

### GPU results on piotr (RTX 5060 Ti, nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04)

Clips streamed at real-time pace in 32 ms chunks; design §7 STT config (float16, beam 5 final,
beam 1 partials every 0.75 s over 4 s).

- Load + warm-up: 9.7 s cold (first process run), 1.6 s with the model already cached in the
  same process. Device-wide peak VRAM during the test: 2302 MiB (12 MiB idle baseline), which
  matches design.md §4.3 (2.3 GB peak).
- Final latency after segment end: 0.16–0.26 s.
- **`hotwords="Ryszardzie"` (as design.md §4.1 step 3 says):** the wake clip is recognized from
  the first partial ("Ryszardzie"). But the model echoes "Ryszardzie" as the whole transcript
  for 3 s of silence and of noise (partials and final), and for the first ~0.75 s of ordinary
  speech (seen once on "O jest test pierwszy"). The echo is always the bare word, so a final
  has an empty remainder and would be cancelled. The partial would still emit a false `wake`
  and set the LISTENING visual.
- **No bias:** no wake-word echo on silence or noise (Whisper's usual "Dziękuję." / "Dzięki za
  oglądanie!" hallucinations instead; VAD keeps those away). The wake clip's final is right, but
  the first two partials read "Przyszardzie…", so with the vocative-only matcher (T-009 review)
  the `wake` partial comes about 1.5 s later.
- `initial_prompt="Ryszardzie"` behaves like `hotwords` (same echo); measured in a one-off run.
- So `hotwords` is an optional argument; the caller (T-010 wiring) decides. **Open design
  question for Maciej**; design.md is unchanged.

## 2026-10-18: hotword decision (manager)

- **Decision: drop the hotword bias.** The `hotwords` option is removed from `stt/whisper.py`
  (nothing else used it), from the T-003 smoke script, and from design.md §4.1 step 3 and the
  §6 STT row. No `initial_prompt` either: the only prompt worth adding was the wake word, and it
  echoed the same way as `hotwords` (measured above).
- The GPU test now has one variant (no bias). It asserts that silence and noise never yield
  "ryszard", and that the wake clip's final and a partial start "Ryszardzie jaka".
- Re-run on piotr, unbiased: load + warm-up 10.0 s; finals "O jest test pierwszy." and
  "Ryszardzie, jaka będzie jutro pogoda?" (final latency 0.17-0.30 s); silence and noise gave
  "Dziękuję." / "Dzięki za oglądanie!" / "Dziękuje za uwagę.", no "Ryszardzie". Passed.
- `tools/stt-gpu-test.sh` now passes `--reinstall-package piotr-voice-hub`: the first re-run
  silently tested the cached wheel of the previous commit (same version, so uv reused it).
- `make check`: 268 passed, 1 skipped.
