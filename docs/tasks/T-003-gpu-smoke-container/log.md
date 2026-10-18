# T-003 log

## 2026-10-18

- Branch `t-003-gpu-smoke-container` off `origin/main` @ `9177b2c` (after PR #2).
- `grok-developer` is in the `docker` group now; `docker info` shows runtimes
  `nvidia`, `runc` (default `runc`) and CDI devices `nvidia.com/gpu=all`. Fixed design.md §3.
- First run failed: `av` 19.0.1 dropped the `metadata_errors` argument that faster-whisper 1.2.1
  passes to `av.open`. Pinned `av<16` (resolves to 15.1.0).
- onnxruntime-gpu 1.31.0 (latest), and also 1.28/1.29/1.30, fail to load the CUDA EP on this
  base image: they are built for CUDA 13 (`libcublasLt.so.13` missing) and ORT silently falls back
  to CPU. 1.22.0, 1.24.4 and 1.26.0 load the CUDA EP. Pinned 1.26.0 (newest working; same version
  as the existing `piotr-wyoming-piper` image).
- Finding, not changed (old project): the running `piotr-wyoming-piper-1` container has both
  `onnxruntime` 1.26.0 and `onnxruntime-gpu` 1.26.0 installed. Its log says
  `CUDAExecutionProvider is not in available provider names` (only Azure, CPU), so Piper runs on
  the CPU despite `--use-cuda`. The hub's Piper service (P1) must install only `onnxruntime-gpu`.

### Measurements (piotr, in container, `tools/gpu-smoke.sh`)

GPU: RTX 5060 Ti, sm_120, driver 595.99.02, 16311 MiB. Host idle use 12 MiB, Ollama idle.
Clip: `piotr_the_ai_server/audio/monitor-test/box3-tts-20260526T110342Z.wav`, 2.10 s, Polish
(Piper TTS), transcribed as "O jest test pierwszy".

faster-whisper 1.2.1, CTranslate2 4.8.3, `large-v3-turbo` float16, `language=pl`, hotwords "Ryszardzie":

| Metric | Value |
|---|---|
| Model load (cached on disk) | 1.77 s (49.1 s on the first run, including the download) |
| Warm-up (first) transcription, beam 5 | 7.84 s (8.10 s on the first run) |
| Beam 5, 5 runs after warm-up | median 0.168 s (0.167–0.169) |
| Beam 1, 5 runs after warm-up | median 0.148 s (0.148–0.150) |
| Device VRAM before model load | 476 MiB (host idle is 12 MiB, so about 464 MiB is this process's CUDA context after imports) |
| Device VRAM loaded / peak | 2561 / 2765 MiB, so the process is about 2.55 GB loaded and 2.75 GB peak |
| Delta vs before load | +2085 MiB loaded, +2289 MiB peak (model + buffers, excluding context) |

The design's §4.3 figures (2.1 GB loaded / 2.2 GB peak, per-process `nvidia-smi` on the host)
match the delta; counting the context, the in-container footprint is about 0.45 GB higher.
Still well within the 3–4 GB hub budget.

onnxruntime-gpu 1.26.0: `CUDAExecutionProvider` active on sm_120 (session providers
`['CUDAExecutionProvider', 'CPUExecutionProvider']`). MatMul+Relu 512×512: first run 425 ms
(includes CUDA init), second 0.4 ms. Max error relative to the output scale is 2.86e-04
(TF32), which is correct. Device VRAM 651 MiB with the session alive.

- `make check` passes. Status: pushed, PR to be opened.

## 2026-10-18 (design follow-up)

- design.md updated with the T-003 results: §3 container/onnxruntime-gpu 1.26.0 confirmed (1.28+ are CUDA 13
  builds and fall back to CPU); §4.3 hub share of about 2.3 GB for large-v3-turbo fp16 (2765 MiB device
  peak); §9 P1 note that the Piper image must install only onnxruntime-gpu. CI on `1ed7956` was green.
