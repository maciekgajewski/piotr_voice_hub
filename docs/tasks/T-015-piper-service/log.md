# T-015: log

## 2026-10-19

- Started from origin/main at aa6f5ea (PR #17, the P1 breakdown, not merged yet: the T-015
  scope is copied into plan.md). Branch `t-015-piper-service`, worktree
  `~/src/piotr_voice_hub-t015`.
- Decisions by Maciej: port 10201 through `tts.uri`; a service in our compose file on the
  pinned CUDA 12.8.1 cuDNN base; the Piper notes (only onnxruntime-gpu 1.26.0 with a build
  assertion, no CPU fallback, pinned voice, HEURISTIC, warm-up, healthcheck).
- Read wyoming-piper 2.5.2 / piper-tts 1.8.0: piper already requests HEURISTIC but with
  default session options (silent CPU fallback); the voice is loaded lazily on the first
  request. Decision: create the session ourselves (HEURISTIC, CPU fallback disabled, provider
  check), warm it up, and hand it to wyoming-piper's voice cache before it serves.
- Voice pinned at `rhasspy/piper-voices` revision c10ece1aade47bb51c153c893d14e5bf8e5b7117;
  its SHA-256 values match the files T-003 measured (`~/.cache/piotr_voice_hub/piper`).
- The old container `piotr-wyoming-piper-1` was not touched (no exec); its cache dir is not
  readable by this user.
- Implemented: `docker/piper.Dockerfile`, `docker/piper/requirements.{in,txt}` (hashes,
  `--no-emit-package onnxruntime`, installed `--no-deps`), `docker/piper/entrypoint.py`,
  `src/piotr_voice_hub/tts/piper_service.py`, compose service `piper` on 10201, unit tests,
  the gated `tests/test_piper_gpu.py` and `tools/piper-gpu-test.sh`; port 10201 in
  `config/hub.example.yaml` and design.md §4, §7, §9.
- Live on piotr, in a separate compose project (`-p piotr-voice-hub-t015`), so nothing in the
  hub's project was touched; only our `piper` service was started.
  - First start failed: `ADD --chmod=644` also gave the directory it created mode 644, so
    nobody could not read the voice (PermissionError, exit 1, Docker restarted it). Fixed
    with `mkdir -m 755` before the ADDs.
  - Second start failed: `session.disable_cpu_ep_fallback=1` makes the session refuse to
    start ("graph nodes that are assigned to the default CPU EP"). With verbose ORT logs: 678
    nodes on CPU, all shape ops (Unsqueeze 146, Gather 121, Concat 119, Slice 60, Reshape 48,
    Sub 45, Mul 39, Where 24, Transpose 24, Equal 24, Squeeze 12, Add 12, Cast 4), which ORT
    always places on CPU, and 2036 on CUDA. Dropped the option; the guard is the provider
    check (CUDA must be the session's first provider).
  - Then healthy. Measurements (GPU at 2240 MiB before: the running hub's 2222 MiB):

    | What | Result |
    | --- | --- |
    | New container start → healthy | 50.7 s: session 1.27 s, warm-up short 48.00 s (first inference), long 0.14 s; "warm in 49.40 s" |
    | Restart of the same container → healthy | 5.1 s: "warm in 2.09 s", first inference 0.71 s |
    | Short sentence (41 chars, 2.76 s audio), Wyoming round trip | 1st 0.084 s, 2nd 0.052 s |
    | Long sentence (191 chars, 12.4 s audio) | 1st 0.180 s (first chunk 0.078 s), 2nd 0.179 s |
    | VRAM | process 692 MiB; device 2240 → 2937 MiB warm (+697), peak 3097 MiB (+857) over the run |
    | Providers | `['CUDAExecutionProvider', 'CPUExecutionProvider']`, onnxruntime 1.26.0 `GPU` |

  - The 48 s is not the cuDNN algorithm search (HEURISTIC is set, and piper-tts sets it
    too): after a restart of the same container the first inference takes 0.71 s, and the
    container has a 111 MB `/tmp/.nv/ComputeCache` (`HOME=/tmp`), i.e. a CUDA JIT compile
    for sm_120, cached per container.
  - `tools/piper-gpu-test.sh`: 3 passed (both sentences twice, 22050 Hz / 16-bit / mono,
    duration and RMS checks; another voice gets an `error`, and the service logs the refusal
    without trying a download).
  - Negative checks with throwaway containers of our image: without a GPU, onnxruntime logs
    "Falling back to ['CPUExecutionProvider']" and the service exits 1 ("voice session is not
    on CUDAExecutionProvider"); with the CPU `onnxruntime` 1.23.2 installed into the image,
    `--check-install` exits 1 (InstallError).
  - Our service stopped and removed afterwards (`docker compose -p piotr-voice-hub-t015
    down`). The hub (`piotr-voice-hub-hub-1`, healthy) and Maciej's containers were not
    touched; no exec into any of them.
