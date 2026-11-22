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

## 2026-11-22

- Independent review of PR #35 (`7b63a9c`, APPROVE; the review was not posted on
  GitHub) listed five non-blocking nits. The 2026-11-13 line above ("Independent review:
  one nit… Re-review: clean.") is the pre-PR script-matcher fix and predates that
  review; it is not the gate. Three were documentation and are fixed in T-025:
  (1) checklist step 0 now stops on any failed expect, including a missing
  `nvidia.com/gpu=all`; (2) step 6 repeats step 2's `--wait` fallback; (3) this
  entry. The other two nits are left: the guard test does not look at service-level
  `devices:` (code, out of T-025 scope), and historical T-012 / T-015 plans still
  mentioning `gpus: all` is correct as history.
- Merged as PR #35 (`c64ebcf`) and deployed on 2026-11-16. Post-deploy hardware
  sessions: `docs/tasks/T-025-docs-followup/hw_session_2026-11-16.md` and
  `hw_session_2026-11-22-results.md`.
