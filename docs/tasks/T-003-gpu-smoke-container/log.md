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

## 2026-10-18 (review of `e9413b7`: changes needed)

Review: https://github.com/maciekgajewski/piotr_voice_hub/pull/3#pullrequestreview-5564287596.
Fixes:
- `whisper_smoke.py` now fails (exit 1) if the warm-up transcript lacks the expected words
  (`--expect-words`, default `test,pierwszy`, case- and diacritics-insensitive), or if the model adds
  less than 1000 MiB after load. Verified on piotr: the default passes, and `kot,pies` fails with
  `WHISPER SMOKE FAILED: ... lacks expected word(s) ['kot', 'pies']` and exit 1.
- `ort_smoke.py` sets `session.disable_cpu_ep_fallback=1`; it still passes.
- Dockerfile installs the requirements before copying the scripts; the build context is now
  `tools/gpu_smoke`. `gpu-smoke.sh` prints a usage message (exit 2) without arguments.
- Static tests pin `onnxruntime-gpu==1.26.0` and `av==15.*`, and check that CPU and GPU
  onnxruntime never share a requirements file.
- **Correction to the first entry:** the 476 MiB "before load" was not this process's CUDA
  context. NVML's v1 memory query counts the driver's 464 MiB device reserve as used
  (`nvidia-smi` shows `memory.reserved 465 MiB, used 12 MiB` on an idle GPU). A bare container
  reads 476 MiB before any CUDA call, and the same after importing ctranslate2/faster_whisper.
  `gpu_info.py` now uses the v2 query (reads 12 MiB at idle). The process's CUDA context is created
  during model load, so it is inside the deltas. The whisper footprint is therefore **2.1 GB loaded /
  2.3 GB peak**, not 2.55 / 2.75 GB (subtracting 12 MiB from the v1 figures double-counted the
  driver reserve). design.md §4.2/§4.3 use these numbers.
- NVML values are device-wide, so the runs were done with Ollama idle (no other compute process).
- New `piper_vram.py` measures the real `pl_PL-bass-high` voice on onnxruntime-gpu 1.26.0
  (random 400 phoneme ids, about 11 s of audio).

Re-run on piotr (`tools/gpu-smoke.sh .../box3-tts-20260526T110342Z.wav`), all OK:

| Check | Result |
|---|---|
| Whisper large-v3-turbo fp16 | load 2.08 s, warm-up 7.95 s, beam 5 median 0.170 s, beam 1 median 0.154 s on the 2.10 s clip; VRAM 12 → 2097 MiB loaded, 2301 MiB peak (+2085 / +2289 MiB) |
| ORT CUDA EP (no CPU fallback) | active on sm_120, relative error 2.86e-04; 187 MiB with the session alive |
| Piper voice on CUDA EP | session load 1.30 s; first run 47.9 s (cuDNN algorithm search), then 0.09–0.10 s per run; VRAM +281 MiB loaded, +1195 MiB peak |

VRAM budget (design.md §4.3): hub process 2.6–2.8 GB (whisper 2.3 GB peak + 0.3–0.5 GB estimated
for concurrent partials) + Piper 1.2 GB = **about 4 GB (3.8–4.0)**. Plus the 0.46 GB driver
reserve, that leaves about 11.8 GB of 16.3 GB for Ollama. Piper's 48 s first inference means the
P1 Piper service needs a startup warm-up.

Image digests used: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc`,
`ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336`.
