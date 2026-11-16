# T-024 log

## 2026-11-13

- Compose today requests the GPU with `gpus: all` on `hub` and `piper` only
  (`docker-compose.yml`). That shorthand is the NVIDIA runtime hook (driver `nvidia`,
  `count: all`). There is no `runtime: nvidia`, no `NVIDIA_VISIBLE_DEVICES`, and no
  `NVIDIA_DRIVER_CAPABILITIES`. `speaker-recognition` has no GPU reservation (CPU torch,
  `docker/speaker.Dockerfile`). `docker-compose.stt-oneshot.yml` only adds the
  `/stt-oneshot` bind. No other compose file.
- Decision: do not set `NVIDIA_DRIVER_CAPABILITIES`. CDI takes devices and libraries from
  `/var/run/cdi/nvidia.yaml` (`nvidia.com/gpu=all`). The variable belongs to the hook this
  change removes. Hub CUDA check is CTranslate2 (`faster-whisper` in `docker/hub.Dockerfile`).
  Piper has no CTranslate2 and no torch; its image has `onnxruntime-gpu` 1.26.0 and numpy,
  so a new process allocates one CUDA `OrtValue`. Speaker recognition is not recreated.
- README describes the product, not the GPU device setup. Deploy text is design.md §9.
- Not deployed. Checklist is `deploy-checklist.md`.
- `make check`: ruff and pyright clean, 990 passed, 8 skipped (GPU tests).
- Independent review: one nit. `tools/check_gpu_cdi.sh` treated a device minor
  (`char-1:195`) as major 195. The matcher now requires `char-<major>` or
  `<major>:<minor>`, so a leading `:` does not count. Re-review: clean.
