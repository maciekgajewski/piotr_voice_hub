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
- Rebased onto origin/main ac1457d (PR #17 merged, review 5569435450); no conflicts.
- Review folding (Maciej): README task notes and this plan now record the decisions:
  - ports: Piper 10201, speaker recognition 2141 (T-018), both configurable;
  - T-020 uses the cues the devices already have with a documented substitute; the firmware
    import stays in P2. Checked the old firmware (read only): neither device exposes the
    follow-up sound as an API service (BOX-3 `play_follow_up_listening_cue` is a script,
    Voice PE `follow_up_listening_sound` a sound file; both only via
    `start_follow_up_listening`, which also starts on-device follow-up listening and must not
    be used). So the substitute has to come from `play_message_end_cue`,
    `play_conversation_timeout_cue` or the `LISTENING` visual alone: noted as T-020's design
    question in the README. No T-020 plan stub: the README note is enough.
  - T-018 can run in parallel with T-015–T-017 (table);
  - the T-015 note: the disable-CPU-fallback option cannot be used for this voice; the guard
    is the CUDA-first-provider check; the piper_vram.py mention is fixed the same way;
  - T-016: the hub-side warm-up is only a reachability (`describe`) check;
  - no host directory for the CUDA compile cache.
- Carry-forward constant names checked in the code: the request read is
  `REQUEST_READ_TIMEOUT_SECONDS` (`hub/playback_http.py`); `STREAM_REQUEST_TIMEOUT_SECONDS`
  also exists (`satellites/esphome.py`, the driver's 5 s fetch wait). The README now names
  both, with their files.
- README row T-015: in review (PR #18).
- Review 5569806153 (independent reviewer, at 58092db): CHANGES NEEDED, two blockers and
  nits. Fixed on the same branch:
  - **B1 (no bound while serving):** `install_voice` now caches a `GuardedVoice` wrapper
    (exposes `.config`), no fork of wyoming-piper. Per `synthesize_wav` call: text over
    `MAX_TEXT_CHARS = 1000` is refused with `TextTooLongError` (wyoming-piper sends it as an
    `error`; the same 1000 as the hub-side cap in T-016, PR #19); a watchdog
    `threading.Timer(SYNTHESIS_TIMEOUT_SECONDS = 30)` logs CRITICAL and `os._exit(1)`s (warm
    191 characters take 0.18 s, so 1000 take about 1 s); an onnxruntime run-time error
    (`Fail`, `RuntimeException`, `EPFail`, `EngineError`) logs CRITICAL and exits, so a sticky
    CUDA error is restarted by Docker (`unless-stopped`). Input errors (`InvalidArgument`, the
    phonemizer, ...) still go back to the client. Unit tests: the cap, the timer (injected
    thresholds and exit), the ORT exit, other errors not exiting, and a subprocess test of the
    real `os._exit(1)` for both the hang and the ORT error.
  - **B2 (LAN exposure):** `--uri tcp://127.0.0.1:10201` in the compose `command` and the
    Dockerfile `CMD`; a unit test checks both, and that piper and the hub both use
    `network_mode: host`, so the hub's `tts.uri: tcp://127.0.0.1:10201` still reaches it.
  - Nit 2: startup also checks the imported module: `onnxruntime.__version__ == "1.26.0"` and
    `get_device() == "GPU"`.
  - Nit 1: after the warm-up, the long sentence is re-timed (`check_warm`) and startup fails
    over `WARM_LIMIT_SECONDS = 1.0` (0.14-0.18 s measured), a guard against CPU placement.
  - Nit 7: the image contains GPL-3.0 piper-tts and espeak-ng; it must not be published to a
    registry without GPL compliance (Dockerfile comment, plan.md, design §9).
  - Nit 8: design §4.3 Piper row: the first inference is a one-time CUDA compile (~48 s),
    cached per container; a restart is healthy in about 5 s.
  - Nit 9: comment in `install_voice`: Describe/Info lists the whole bundled catalog as
    installed; its contents are unchanged.
  - README: the T-020 note records Maciej's decision (no sound, only the `LISTENING` visual
    for the follow-up-ready state until the P2 firmware adds the real cue); the T-016 note says
    the service refuses calls over 1000 characters.
- Live check on piotr, separate compose project `-p piotr-voice-hub-t015`, only our `piper`
  service (the hub `piotr-voice-hub-hub-1` and Maciej's containers untouched, no exec):
  - New container → healthy in about 51 s: session 1.21 s, warm-up short 48.37 s (first
    inference), long 0.15 s, warm check 0.11 s (limit 1 s), "warm in 49.85 s".
  - `ss -ltnp`: `127.0.0.1:10201` only; a connect to the LAN address 192.168.0.3:10201 is
    refused.
  - `tools/piper-gpu-test.sh`: 4 passed, twice. Warm latency unchanged: 41 chars 0.053-0.086 s,
    191 chars 0.176-0.237 s (before: 0.052-0.084 s and 0.179-0.180 s). A 1064-character text
    without a sentence end gets `error` `TextTooLongError` (the service logs "refused a
    synthesis of 1064 characters"), and the next request works; restart count 0.
  - Stopped and removed (`docker compose -p piotr-voice-hub-t015 down`).
- `make check` green 3× (547 passed, 5 skipped: the GPU tests).
- Follow-up decided on PR #18 (manager): Describe lists only the served voice, so the hub's
  startup describe check (T-016) fails on a wrong `tts.voice`. No fork: `advertise_only`
  wraps `wyoming_piper.__main__._piper_info` (wyoming-piper builds Info with it once at
  startup and on every Describe, looked up in the module), keeps only the configured voice
  and fails startup if the Info would not list exactly that voice. The nit-9 comment is
  replaced; plan.md and design §9 updated. Unit tests: only the configured voice on every
  Describe, a voice missing from the Info fails, another wyoming-piper fails, and `main()`
  installs the filter. `tests/test_piper_gpu.py` gained a Describe test.
- Live re-check on piotr (`-p piotr-voice-hub-t015`, only `piper`; hub and Maciej's
  containers untouched): healthy (warm in 49.65 s, warm check 0.11 s), `127.0.0.1:10201`
  only; `tools/piper-gpu-test.sh` 5 passed: Describe lists exactly `["pl_PL-bass-high"]`,
  synthesis works (41 chars 0.027-0.087 s, 191 chars 0.175-0.183 s), the other-voice and
  over-1000-character `error`s as before; restart count 0. Stopped and removed (`down`).
- `make check` green 3× (550 passed, 6 skipped: the GPU tests).
