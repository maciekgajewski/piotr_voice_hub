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
