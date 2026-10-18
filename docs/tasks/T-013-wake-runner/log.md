# T-013 log (append-only)

## 2026-10-18: start

- Branch `t-013-wake-runner` from origin/main c39d7ef (PR #9 merged).
- `docker ps -a` on piotr: running immich, piotr-ollama-1, piotr-speaker-recognition-1,
  piotr-wyoming-piper-1. `piotr-ai-server-1` exited 3 months ago (its config is
  `/home/maciek/ai_server_config.yaml` → `/config/ai_server.yaml`). So the old server holds no
  voice-assistant subscription. Nothing was stopped, restarted or reconfigured.
- Satellites: see plan.md. mDNS shows voice-pe-02 and voice-pe-03 online (ESPHome 2026.4.5,
  Noise encryption); bedroom-01 and box3-01 are not advertised.
- API keys are not available to this account (`secrets.yaml` absent from the checkout, the old
  config is under /home/maciek and not readable), so a live connect is left to Maciej.

## 2026-10-18: implementation

- `src/piotr_voice_hub/wake_test.py` (`piotr-voice-hub-wake-test`), `tools/wake-test.sh`,
  `tests/test_wake_test.py` (8 tests, fake microphone and STT). `make check`: 387 passed,
  1 skipped.
- Runs in Docker, not host `uv run`: the host has no cuBLAS (T-008).
- Container smoke test on piotr with a config pointing voice-pe-03 at 127.0.0.1: STT loaded on
  the GPU in 10.5 s (cached model), connect refused, re-arm backoff 1, 2, 4, 8, 16, 30, 30 s,
  SIGINT closed the driver with no traceback.
- In the container (`--network host`) aioesphomeapi's zeroconf resolver finds
  piotr-voice-pe-02.local (192.168.0.108) and piotr-voice-pe-03.local (192.168.0.115); libc
  `getaddrinfo` does not resolve `.local` there, which aioesphomeapi does not need.
- No live satellite test: the API keys are not readable by this account (see start entry). No
  connection to a real satellite was attempted.
- `config/hub.example.yaml` and the design.md §7 example: voice-pe-02/03 addresses now carry
  the real `piotr-` prefix.
- `docs/tasks/README.md`: T-007/T-008/T-009 marked merged; T-013 marked throwaway (not the seed
  of T-012: that main is built on T-010's session); carry-forwards from the PR #9 review added
  (command serialization in T-010, the two driver follow-ups, the P2 audio-liveness check).

## 2026-10-18: review follow-up (PR #10 review 5564897137, merged as 1a4b342)

Branch `t-013-followup` from origin/main 1a4b342. Nits 3, 5 and 7 are left as they are.

- 1: on Ctrl-C each armed satellite gets `StopListening(…, "shutdown")` and the idle visual,
  best effort within 3 s, then the driver is closed, so the LEDs do not stay in listening. A
  satellite that is not armed is only closed (no reconnect just for the visual).
  `run_until_cancelled` in wake_test.py; tests for the clean stop and for a failed stop that
  still closes. The checklist's Ctrl-C item now names this.
- 2: an exception other than `MicrophoneUnavailable` on one satellite is logged with its
  traceback and handled like unavailable (close, back off, re-arm); the other satellites keep
  running. Test with two satellites. The fake microphone now records overlapping commands
  instead of asserting inside the runner, since the runner recovers from assertion errors.
- 4: `_start_segment` asserts no segment is open (the open segment now lives on the runner);
  test: a second `SpeechStarted` is a loud error, the open STT session is closed, re-arm.
- 6: plan.md says `SatelliteRunner.run` (not `run_satellite`), and spells out the exit codes
  as the code has them: a model load failure is an uncaught exception from `stt.start()`
  (exit 1); load or warm-up timeouts and model errors in a segment exit 70.
- 8: `chmod 600` on the config in plan.md's manual steps, the `tools/wake-test.sh` header and
  the PR checklist. Manual steps now use `git pull` on main, no branch checkout.
