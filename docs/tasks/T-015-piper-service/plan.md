# T-015: GPU wyoming-piper service: plan

**Status:** implemented as below and measured on piotr (2026-10-19). No design question
changes the implementation; the open points are listed at the end.

## Scope

From the P1 breakdown (PR #17, docs/tasks/README.md, not merged yet, so copied here):

> **T-015 piper-service** (depends on T-003, T-012): the hub's own `wyoming-piper` compose
> service on the GPU: an image with only `onnxruntime-gpu` 1.26.0 (no `onnxruntime`), the
> voice pinned by revision and hash, CUDA EP with no CPU fallback, HEURISTIC cuDNN algorithm
> search; smoke test on piotr.
>
> - Image: install only `onnxruntime-gpu==1.26.0`. 1.28+ are CUDA 13 builds and silently fall
>   back to CPU on the CUDA 12.8 image, and having `onnxruntime` installed too makes it run on
>   CPU (design §3, §9).
> - Voice `pl_PL-bass-high`: download it at build time from a pinned Hugging Face revision
>   and check its SHA-256. No `--update-voices` at runtime.
> - No CPU fallback: the session must fail loudly when the CUDA EP is not active.
> - First inference: T-003 measured 48 s (cuDNN EXHAUSTIVE algorithm search), then 0.1 s. Use
>   HEURISTIC and/or a text-length cap per call (T-016); measure a first call and a call with
>   a new length.
> - Own service, not a reuse of the old `piotr-wyoming-piper-1` (old project, port 10200).

Decisions by Maciej (2026-10-19):
- The service listens on **10201**; the hub reaches it through `tts.uri`, so it does not clash
  with the old container on 10200.
- A separate service in our compose file (design §9: "one compose file, three services"), on
  the CUDA 12.8.1 cuDNN base pinned by digest.
- Also: a build-time assertion that the CPU-only `onnxruntime` is absent, a startup check of
  the session's providers, warm-up at startup, a healthcheck.

Out of scope: the hub's Wyoming client, the text cap per call and the hub's own warm-up call
(T-016); the hub's `depends_on` the service (T-016/T-017); speaker recognition (T-018);
deploying (T-022). The running hub, its checkout and Maciej's containers are not touched.

## What exists

- **Old project** (read only): `docker-compose.yml` runs `wyoming-piper --voice <voice> --uri
  tcp://0.0.0.0:10200 --data-dir/--download-dir /app/.piper-cache --update-voices --use-cuda
  --samples-per-chunk 1024`, built from `docker/ai-server.Dockerfile`; its TTS image
  installs `onnxruntime-gpu` (unpinned) after removing `onnxruntime`. The voice is
  `pl_PL-bass-high` (design §4.3, §7).
- **T-003** (`tools/gpu_smoke/piper_vram.py`, reading reference only): the voice on
  onnxruntime-gpu 1.26.0 CUDA EP: 0.3 GB loaded, 1.2 GB peak, first run 48 s, then 0.1 s.
- **wyoming-piper 2.5.2** (latest, with piper-tts 1.8.0 and wyoming 1.10.2):
  - `PiperVoice.load(..., use_cuda=True)` already asks for
    `("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})`, but with default
    `SessionOptions`, so onnxruntime silently runs on CPU when the CUDA EP fails to load.
  - The voice is loaded lazily on the first request into a module-level one-voice cache
    (`wyoming_piper.handler._VOICE`, `_VOICE_NAME`); another voice name loads (and may
    download) another voice.
  - A voice whose files are in `--data-dir` is never downloaded (`ensure_voice_exists`).
  - `python -m wyoming_piper.health_check --uri ...` does a Describe/Info round trip.
  - `piper-tts` depends on the CPU `onnxruntime`.

## Implementation

1. **Image `docker/piper.Dockerfile`** (build context: repo root, as the hub's):
   - `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04` and `uv` 0.13.1, both pinned by the same
     digests as `docker/hub.Dockerfile`; Python 3.12 from uv.
   - `docker/piper/requirements.txt`: compiled with hashes from `requirements.in`
     (`wyoming-piper==2.5.2`, `piper-tts==1.8.0`, `wyoming==1.10.2`,
     `onnxruntime-gpu==1.26.0`) with `--no-emit-package onnxruntime`, installed with
     `--require-hashes --no-deps`, so the CPU `onnxruntime` never gets in.
   - Build-time assertion (`--check-install`): `onnxruntime-gpu` is 1.26.0 and `onnxruntime`
     is not installed; otherwise the build fails.
   - The voice: `ADD --checksum=sha256:…` of `pl_PL-bass-high.onnx` and `.onnx.json` from
     `rhasspy/piper-voices` at revision `c10ece1aade47bb51c153c893d14e5bf8e5b7117` (main on
     2026-10-19; the files are unchanged since 2026-03-09). SHA-256: `.onnx`
     `73b84089…bde72c2`, `.onnx.json` `7bb41aa1…c1e59` (the same files T-003 measured). They
     are baked into `/opt/piper/voices`, world-readable; the container runs as nobody.
2. **`src/piotr_voice_hub/tts/piper_service.py`** (stdlib and onnxruntime only, so the hub's
   tests run it on the CPU onnxruntime; the image copies it next to the entry point):
   - `check_install()`: the install check above, also run at every start.
   - The ONNX session is created by us, not by `PiperVoice.load`: only the CUDA EP is
     requested, with `cudnn_conv_algo_search=HEURISTIC`. Startup fails unless
     `CUDAExecutionProvider` is available and is the session's first provider (when the CUDA
     EP cannot load, onnxruntime runs the whole model on CPU and lists only the CPU EP).
   - **Not** `session.disable_cpu_ep_fallback=1` (tried on piotr): ORT always places this
     voice's 678 shape ops (Unsqueeze, Gather, Concat, Slice, …) on CPU, next to 2036 nodes
     on CUDA, so with that option the session refuses to start. The provider check is the
     "no CPU fallback" guard.
   - **HEURISTIC, decided:** set it ourselves in the provider options (piper-tts 1.8.0 does
     the same, but we do not depend on that). No wrapper or `sitecustomize` is needed, since we
     create the session. The text cap per call stays in T-016 as a second guard (each call is
     also split into sentences by wyoming-piper).
   - Warm-up before serving: synthesize two fixed Polish sentences (a short and a long one),
     each bounded by a timeout; a timeout or error exits 1. The port opens only after the
     warm-up, so the healthcheck is green only when the service is warm.
   - The warmed voice goes into wyoming-piper's one-voice cache, and loading any other voice
     raises (no runtime downloads, no unchecked sessions). The pinned wyoming-piper is
     checked for the attributes this relies on.
   - Then wyoming-piper's own `run()` with `--voice --uri --data-dir --use-cuda
     --samples-per-chunk 1024` (no `--update-voices`); `WYOMING_PIPER_ARGS` must be unset.
3. **`docker/piper/entrypoint.py`:** wires the real piper and wyoming-piper into it
   (`PiperVoice` built from our session, synthesis into an in-memory WAV).
4. **Compose service `piper`** in `docker-compose.yml`: `network_mode: host`, `gpus: all`,
   `restart: unless-stopped`, `init: true`, json-file logs 5 × 10 MB, a healthcheck with
   `wyoming_piper.health_check` on 127.0.0.1:10201, `--uri tcp://0.0.0.0:10201`.
5. **Port 10201:** `config/hub.example.yaml`, design.md §4 diagram, §7 and §9 port table.

## Tests

- Unit (`tests/test_piper_service.py`, in `make check`): install check cases, provider checks,
  session options, warm-up timeout and failure, the voice cache hand-over and refusal, the
  wyoming-piper argv, and `main()` with fakes.
- GPU-gated integration (`tests/test_piper_gpu.py`, marker `gpu`): runs only with
  `PIOTR_VOICE_HUB_PIPER_URI` set (`tools/piper-gpu-test.sh`). It speaks the Wyoming protocol
  to the running service, synthesizes a short and a ~200-character Polish sentence twice each,
  and checks the format (22050 Hz, 16-bit, mono), the duration and that the audio is not
  silent; it prints the latencies.
- Live on piotr: build, start only our `piper` service (in a separate compose project name,
  so nothing in the hub's project is touched), measure time to ready, latencies, VRAM and the
  provider, then stop it.

## Acceptance

- `make check` green 3×, CI green.
- On piotr: the service is healthy on 10201, logs `CUDAExecutionProvider`, the integration
  test passes, and the measurements are in log.md.

## Findings on piotr (2026-10-19, details in log.md)

- The 48 s first inference is **not** the cuDNN algorithm search: it is the same with
  HEURISTIC. It is a one-time CUDA JIT compile (onnxruntime-gpu 1.26.0 ships no sm_120 code
  for some kernels), cached in the container at `$HOME/.nv/ComputeCache` (111 MB). A restart
  of the same container is warm in 2 s; only a new container (a deploy) pays the 48 s, and
  the warm-up absorbs it before the port opens. HEURISTIC stays (it is piper's own choice
  too), and the text cap per call stays in T-016 for bounded calls, not for this.

## Open points (do not change the implementation)

- Persisting the CUDA JIT cache on a host directory (`CUDA_CACHE_PATH`, like the hub's model
  cache) would make a new container ready in about 5 s instead of about 50 s. Not done: it
  is a new host directory, and 50 s once per deploy is harmless.

- The hub's own warm-up call in T-016 is then only a connectivity check: the service warms
  itself, which also covers a restart of the service alone.
- A request with another voice name gets a Wyoming `error` (the service serves exactly one
  voice).
