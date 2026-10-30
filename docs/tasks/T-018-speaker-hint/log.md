# T-018: log

## 2026-10-30

- Branch `t-018-speaker-hint` from `origin/main` bfcf38f, worktree
  `~/src/piotr_voice_hub-t018`.
- Read design.md §4.1, §4.3, §5–§7, §9, the README T-018 row and its ports note, and the old
  project's `ai_server/speaker_recognition/` and session use (read only). plan.md written.
- Implemented per plan: `speaker/` (interfaces, client, service), `docker/speaker*`, compose
  service `speaker-recognition`, session and main wiring, `speaker_recognition.url` check.
- Requirements: `uv pip compile` (command in `requirements.in`) with the PyTorch CPU index
  first: torch/torchaudio 2.9.1+cpu, speechbrain 1.1.2, numpy 2.5.4; `--emit-index-url`, and
  `--index-strategy unsafe-best-match` at install. Model: spkrec-ecapa-voxceleb @ 0f99f2d,
  five files by SHA-256.
- First image run failed: SpeechBrain links `label_encoder.txt` → `label_encoder.ckpt` inside
  the model dir, which the non-root user cannot write. Fix: the Dockerfile loads and warms the
  model once at build time as root (also a build-time check that it loads offline).
- Profile paths: a relative path is taken under `--profiles-dir`; `resolve()` then
  `is_relative_to`, so `..` and symlinks out of the mount are refused (400).
- Service reads the body before validating headers: a 400 over unread data would reset the
  connection instead of answering.
- Image `piotr-voice-hub-speaker:t018`: 1.99 GB on disk (484 MB compressed content). Run on
  127.0.0.1:21411 with `--read-only --tmpfs /tmp`, as uid 1002, profiles from the test WAVs
  (`speech` from the first half of `speech_pl_16k.wav`, `tone` from `alarm_tone_16k.wav`),
  through the hub's `HttpSpeakerRecognition`:
  - model load + warm-up 0.2 s (after the build-time load); healthy at once;
  - second half of the same recording: `Speech` score 0.630, confidence 0.66, 26–36 ms;
  - `wake_pl_16k.wav` (same Piper voice, other sentence): score 0.441, null; 39 ms;
  - 15.6 s of audio: 190–200 ms (CPU, Ryzen 5 5600);
  - a path outside /profiles: 400, WARNING, null; nothing listening: WARNING, null;
    timeout 0.01 s: WARNING "no answer within", null;
  - RAM 308 MiB idle, 432 MiB after requests; no GPU, so no VRAM;
  - the compose healthcheck command passed inside the container.
  Container removed after the run.
- `ss -ltn`: the service listened on 127.0.0.1 only.
- Production config checked with this branch's loader (nothing printed but the result): valid;
  it still points at the old service (port 2140) with one profile under `/profiles/`, a path
  the old container does not have, so until the url changes every hint is null with a WARNING.
- Compose: SPEAKER_PROFILES bind with `create_host_path: true`, so a missing directory never
  stops the whole project (the hint is optional).
- `make check` green: 789 passed, 8 skipped (GPU/Piper opt-in). `tests/test_speak_gpu.py`
  also run against our Piper (`PIOTR_VOICE_HUB_PIPER_URI=tcp://127.0.0.1:10201`): passed.
