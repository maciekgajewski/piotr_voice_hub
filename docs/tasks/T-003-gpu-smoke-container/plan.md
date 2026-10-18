# T-003: GPU smoke test in the container

## Goal

Close the open risk in design.md §3: confirm, inside a container on the hub's planned CUDA base
image, that faster-whisper/CTranslate2 and onnxruntime-gpu (Piper's runtime) work on the
RTX 5060 Ti (sm_120). Measure real numbers.

## Scope

- `docker/gpu-smoke.Dockerfile`: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04` + uv + Python 3.12,
  two venvs (faster-whisper; onnxruntime-gpu), hash-pinned requirements.
- `tools/gpu_smoke/whisper_smoke.py`: load `large-v3-turbo` float16, warm up, time beam 5 and
  beam 1 on a clip, sample device VRAM (NVML).
- `tools/gpu_smoke/ort_smoke.py`: tiny MatMul+Relu ONNX model on `CUDAExecutionProvider` only;
  fail if ORT falls back to CPU or the result is wrong.
- `tools/gpu_smoke/piper_vram.py`: VRAM and latency of the real Piper Polish voice on the CUDA EP
  (added after review, for the VRAM budget).
- `tools/gpu-smoke.sh`: build and run all checks on the host GPU.
- Static tests: base image matches the design, requirements are pinned with hashes.
- Fix the stale `docker` group line in design.md §3.

## Out of scope

- The hub image, compose, any hub code or project dependencies.
- Changing the running Piper container.

## Acceptance

- `tools/gpu-smoke.sh <clip>` prints `WHISPER SMOKE OK` and `ORT SMOKE OK` on piotr.
- Measured numbers recorded in `log.md`. `make check` and CI pass.
