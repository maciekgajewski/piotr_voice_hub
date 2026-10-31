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

## 2026-10-30

- PR #27 review of 5b473d6: CHANGES NEEDED (B1, S2; nits N3–N7). Fixes:
  - **B1:** four malformed answers escaped `recognize()` and stopped the hub: a 400-digit score
    (OverflowError), a score over 4300 digits (ValueError), `[` × 60000 (RecursionError) and
    `Content-Length: \xb2` ('²' passes `isdigit()`, not `int()`). Now: Content-Length (and the
    status code) must be ASCII digits, in the client and the service; `parse_result` turns
    those JSON and float errors into `SpeakerRecognitionError`; and `recognize()` ends with
    `except Exception` → one WARNING, null (CancelledError still passes). The four cases are in
    `test_a_bad_answer_is_none_and_one_warning`; all four failed on 5b473d6 with exactly those
    exceptions. A catch-all test (a monkeypatched `parse_result` raising KeyError) and a
    service test for `Content-Length` '²', '-1', '1e3' and none.
  - **S2:** `satellite_status: processing` is emitted after the hint, right before
    `transcript_final` (the PROCESSING visual stays before the hint), so a speak or release
    sent on `processing` is no longer BUSY. Test: a 0.1 s hint, `release()` at once on
    `processing` is taken.
  - **N3:** test for a score exactly at the threshold (recognized, confidence 0.5), and just
    under a higher one (null).
  - **N4:** one example profile path, `/profiles/maciek.npz`, in design.md §7,
    `config/hub.example.yaml` and the config test.
  - **N5:** compose `cap_drop: [ALL]`, `no-new-privileges:true`; torch 2 intra-op threads,
    1 inter-op (entry point, logged at startup). Why 2: the 5600 has 6 cores / 12 threads
    shared with the hub (Silero, Whisper's CPU side), Ollama and the other services; 2
    threads keep a 15.6 s request (the longest the hub sends) at about 0.4 s, inside the 1 s
    timeout and mostly hidden behind the accepted cue.
  - **N6:** no change (exact-score ties), as decided.
  - **N7:** no code change: checklist line for the broken-pipe traceback after a hub timeout.
- Image re-check (`piotr-voice-hub-speaker:t018`, 127.0.0.1:21411, read-only, cap-drop ALL,
  no-new-privileges, uid 1002): builds; healthy; `torch threads: 2`; warm in 0.3 s;
  CapEff 0, NoNewPrivs 1. Same-voice clip: Speech 0.663 in 42–48 ms; 2.6 s: null in about
  78 ms; 15.6 s: 390–440 ms (was about 200 ms with all threads); RAM 429 MiB. Loopback only.
  Container and image removed by name.

## 2026-10-31 (hardware, e60e03a)

- Hardware session on production e60e03a, reported by the manager; voice-pe-03 (study) only,
  at Maciej's request. Times UTC.
- Steps 4/5: four wakes, two of them split; all came back as `Maciek`, confidence 0.54–0.66.
- Step 6: another person speaking scored 0.118 and came back as `null`.
- Step 7: with the service stopped (the manager stopped and started speaker-recognition at
  about 16:28 UTC, 17:28 CET), one WARNING "hint is null", the reply played; the service was
  started again and is healthy.
- Result: **PASS**. The voice-pe-02 run is not part of this session.
