# T-024: CDI GPU reservation

## Goal

Keep `/dev/nvidia*` (device majors 195 and 511) in the hub and Piper device allowlists
across `systemctl daemon-reload`. On piotr, Docker uses the systemd cgroup driver and the
NVIDIA runtime hook. Any daemon-reload (apt's daily upgrade runs one) drops devices the
hook added beside the scope unit. Processes that already hold the GPU keep working; the
next CUDA init fails with `CUDA failure 100: no CUDA-capable device` until the container
is recreated. The hub, Piper and speaker recognition only survive today because they load
their models once at startup.

## Scope

- `docker-compose.yml`: replace `gpus: all` on `hub` and `piper` with a CDI reservation,
  driver `cdi`, `device_ids: ["nvidia.com/gpu=all"]`, `capabilities: [gpu]`.
- Confirm `docker-compose.stt-oneshot.yml` does not reserve a GPU (it only adds a bind).
  No other compose file in the repo.
- `speaker-recognition` stays CPU-only: no device reservation (design.md §9, T-018).
- A pytest that parses every compose file and checks the reservation.
- `docs/design.md` §9: how the GPU is requested. README has no deploy text for the GPU
  setup, so it is unchanged.
- `docs/tasks/T-024-cdi-gpu/deploy-checklist.md` for a later deploy. This task does not
  deploy.
- `tools/check_gpu_cdi.sh`: read-only check of DeviceAllow, `nvidia-smi`, and a new-process
  CUDA init for the containers it is given.

## NVIDIA_DRIVER_CAPABILITIES

Not set, and not kept. Nothing in the compose files sets it today. CDI does not read it.
`nvidia.com/gpu=all` in `/var/run/cdi/nvidia.yaml` (regenerated at boot) is what injects
the devices and the driver libraries. The variable is only for the legacy NVIDIA runtime
hook, which is the hook this change stops using. `NVIDIA_VISIBLE_DEVICES` and
`runtime: nvidia` are the same: not set today, not added. There is no `count:` on the
reservation (CDI uses `device_ids`, and `count` would request the hook's "all" devices).

The hub needs CUDA compute (CTranslate2) and `nvidia-smi`. Piper needs the CUDA execution
provider and `nvidia-smi`. The default `nvidia.com/gpu=all` spec includes those. Graphics,
video, display and compat32 are not used. A narrower spec would be a host
`nvidia-ctk cdi generate` choice, not a compose environment variable.

## Out of scope

- Deploying, rebuilding images, or restarting Docker.
- `daemon.json` (`"features": {"cdi": true}` is root work, checklist step 0 only).
- One-shot `docker run --gpus all` in `tools/gpu-smoke.sh`, `tools/stt-gpu-test.sh` and
  `tools/wake-test.sh`. Those processes exit before a later daemon-reload matters.
- Image, port, volume, user, command, or service behaviour.

## Acceptance

- `make check` passes. The new test fails if a GPU service is not exactly that CDI
  reservation, or if any service sets `runtime: nvidia` or `driver: nvidia`.
- Checklist covers the six deploy steps. Production is not deployed in this task.
