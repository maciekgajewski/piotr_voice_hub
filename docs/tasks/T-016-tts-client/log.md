# T-016: log

## 2026-10-19

- Branch `t-016-tts-client` from `origin/t-015-piper-service` (58092db): T-016's live test
  needs PR #18's compose service and `tts.uri` on 10201. The PR's base is `main`; rebase after
  #18 merges. Worktree `~/src/piotr_voice_hub-t016`.
- Read the old `ai_server/microphones/tts.py` (read only): `synthesize` over
  `wyoming.client.AsyncTcpClient`, no timeouts, unknown events skipped, a validation synthesis
  at start. Ported the request/response pattern; added bounds, strict events and the cap.
- Text cap: design.md says nothing, so per the brief: reject over the cap (1000 characters)
  with `TtsTextError`, no splitting. The README's T-016 note said "split", and is updated.
- Implemented as in plan.md: `tts/interfaces.py`, `tts/wyoming_piper.py` (`wyoming==1.10.2`
  for the framing and event classes, our own connection with bounds), `tts.uri` validated in
  config (`parse_tcp_uri`), `run_hub` awaits `tts.start()` first, compose `depends_on` piper
  healthy. A `clock` parameter was dropped as unused. `wyoming.audio` imports `audioop`
  (deprecated in 3.12, with its own fallback for 3.13); the warning is left visible.
- Unit tests: `tests/test_tts.py`, 31 tests against a fake Wyoming server on loopback.
- Live, on piotr: our `piper` alone in its own compose project
  (`HUB_CONFIG=/dev/null HUB_USER=… docker compose -p piotr-voice-hub-t016 up -d piper`),
  port 10201 free before; new container healthy after 51.2 s (T-015: 50.7 s).
  `tools/piper-gpu-test.sh`: 4 passed (T-015's 3 and T-016's 1). Client measurements:
  - `start()` (connect, describe → info): 0.010 s; 5 more: 0.009–0.014 s.
  - 41 characters: first chunk 0.074 s, total 0.076 s, 2.89 s of audio (twice: 0.073/0.075).
  - 191 characters: first chunk 0.077 s, total 0.176 s, 12.54 s of audio (twice: 0.076/0.177).
  - 968 characters (near the cap): first chunk 0.075 s, total 0.903 s, 67.43 s of audio.
  - 1001 characters: `TtsTextError`, no connection.
  - A closed port: `TtsError connect to 127.0.0.1:10209 failed: ConnectionRefusedError`.
  - **Finding:** `start()` with voice `pl_PL-other` fails, but with `pl_PL-gosia-medium` it
    passes: wyoming-piper's `info` lists its whole catalogue (about 170 voices) as installed.
    Synthesis with it is then `TtsError service error: only the preloaded voice is served
    (asked for 'pl_PL-gosia-medium') (code RuntimeError)` after 0.001 s. The voice check is
    kept (it catches a voice outside the catalogue) and documented; the error message now
    gives the count, not the list. Question in plan.md.
  - `docker compose -p piotr-voice-hub-t016 down`: removed; 10201 free again. No other
    container touched; the running hub was not touched.
- PR #19 opened (head 531496c, CI green).
- PR #18 merged as e8a5679. Its final version describes only the served voice
  (`advertise_only`), guards each synthesis (`GuardedVoice`: 1000-character cap, 30 s
  watchdog) and binds 127.0.0.1:10201. Rebased onto `origin/main` with
  `git rebase --onto origin/main 58092db` (dropping #18's two commits); one conflict, the
  README T-016 note (#18 had "split … N ≤ 1000", T-016 "reject"), resolved to reject, noting it
  is the service's own limit.
- Startup check tightened: `start()` now requires the installed voices in `info` to be exactly
  `[tts.voice]`; the `TtsError` names both ("tts.voice is 'X', but Piper at … describes
  [...]"). `hub/main.py` `check_tts` logs it as one CRITICAL line ("TTS startup check failed,
  exiting: …") and `run_hub` returns 1. Tests: five describe variants in `test_tts.py`, three
  `check_tts` tests in `test_main.py`, a wrong-voice `start()` in the GPU test. The earlier
  plan question (a wrong voice is not caught at startup) is resolved; plan and docstring updated.
- PR #18 re-review nits (review 5570089492), a separate commit:
  - **N1:** `NEAR_CAP_TEXT`, one Polish sentence of 949 characters (no `.?!` inside), is the
    third warm-up text; the warm check re-times `WARM_CHECK_TEXT` (the ~200-character one), no
    longer `WARMUP_TEXTS[-1]`.
  - **N2: `faulthandler.dump_traceback_later(timeout, exit=True)` replaces the
    `threading.Timer`** in `GuardedVoice` (`_arm_watchdog`; disarmed in `finally` with
    `cancel_dump_traceback_later`). Why:
    - It runs in a C thread that needs no GIL, so it fires even when the stuck call holds the
      GIL (for example a hang inside piper's espeak bridge); the Timer needed the GIL to run.
    - It dumps every thread's stack to stderr (the container log) before `_exit(1)`, so a hang
      is diagnosable afterwards; the Timer only logged one CRITICAL line.
    - Its single per-process slot fits: wyoming-piper 2.5.2 runs each `synthesize_wav`
      synchronously on its event-loop thread under `_VOICE_LOCK`, one at a time, and nothing
      else in the piper process uses faulthandler (the hub's own watchdog is another process).
    - Cost: no CRITICAL log line of our own at the timeout; faulthandler's dump starts with
      "Timeout (0:00:30)!" and names the stuck frame (`synthesize_wav`). The subprocess test
      checks exactly that, and exit code 1.
    The warm-up's own bound (a worker thread plus `join(timeout)`) is unchanged.
  - **N3:** `WARM_LIMIT_SECONDS` 1 s → 2 s.
  - **N4:** `_timed_synthesis` takes a label: the warm check's thread is
    `piper-warm-check` and its lines say "warm check"; the compose comment's long line is
    reflowed; the `MAX_TEXT_CHARS` comment says the T-016 client rejects (not splits).
  - Unit tests updated: three warm-up texts and their lengths, the warm-up log line, the warm
    check's own label (also when hung), arm → synthesize → disarm order, no arm for a refused
    text, disarm on failure, the faulthandler defaults, the real exit in a subprocess.
- Live check (13:28–13:31 Europe/Warsaw):
  - **Mistake, fixed:** port 10201 was already taken by the redeployed hub's own
    `piotr-voice-hub-piper-1` (project `piotr-voice-hub`, from main). My
    `docker compose -p piotr-voice-hub-t016 build piper` re-pointed the shared
    `piotr-voice-hub-piper:latest` tag at the T-016 build, and my `up` started a container
    that loaded the voice; I removed it within about 10 s, before its warm-up ended (it never
    bound a port). The production container was not touched and kept running on its own
    image. The `latest` tag could not be pointed back at that image by ID (containerd image
    store), so I rebuilt it from a clean temporary worktree at `origin/main` (e8a5679;
    `docker build -f docker/piper.Dockerfile -t piotr-voice-hub-piper:latest .`, all cached,
    then the worktree removed), so `latest` is main's content again (new ID 6bdce4c2…).
    Lesson: this compose file's `image:` names are shared by every project, so a test project
    must use its own image tag and port.
  - Then: the T-016 image tagged `piotr-voice-hub-piper:t016`, started as
    `-p piotr-voice-hub-t016` with an override file (that image, `--no-build`, port 10211 in
    the command and the healthcheck).
  - New container healthy after 51.0 s; service log: voice loaded 1.26 s; warm-up 24 chars
    47.89 s (the one-time JIT), 194 chars 0.14 s, **949 chars 0.47 s** (58.5 s of audio), warm
    check 0.14 s (limit 2 s); warm in 49.89 s.
  - Raw describe: `info`, tts `piper` → `[('pl_PL-bass-high', True)]`, one voice.
  - GPU tests against 10211: 6 passed. T-016 client: `start()` 0.002 s; 41 chars
    0.052–0.074 s first chunk; 191 chars 0.176–0.178 s total; 968 chars 0.929 s total
    (66.5 s of audio); `start()` with `pl_PL-gosia-medium` fails.
  - The real hub CLI with a copy of `config/hub.example.yaml` (placeholder keys) changed to
    `tts: {uri: tcp://127.0.0.1:10211, voice: pl_PL-gosia-medium}`: exit 1 after 0.35 s with
    `CRITICAL piotr_voice_hub.hub.main: TTS startup check failed, exiting: tts.voice is
    'pl_PL-gosia-medium', but Piper at 127.0.0.1:10211 describes ['pl_PL-bass-high'] (it must
    describe exactly the configured voice)`. The check runs first, so no satellite was
    contacted. Copy deleted.
  - Torn down: `down` (container removed), the `:t016` image deleted, 10211 free. The hub,
    production piper and Maciej's containers kept running untouched.
