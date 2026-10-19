# Tasks

One task = one directory `T-NNN-slug/` with `plan.md` and an append-only `log.md`, one branch,
one PR (AGENTS.md). Order is the dependency order.

## P0 (design.md §11)

| Task | Scope | Status |
| --- | --- | --- |
| T-001 bootstrap | Repo skeleton, AGENTS.md, `make check`, CI | merged |
| T-002 design-status | design.md status line | merged |
| T-003 gpu-smoke-container | faster-whisper and onnxruntime-gpu on sm_120 in the CUDA 12.8 image | merged |
| T-004 config | Hub config dataclasses, strict YAML loader, CLI entry point | merged |
| T-005 satellite-protocol | Satellite messages, protocol state machine, conformance checks | merged |
| T-006 vad | Silero VAD with fixed hysteresis | merged |
| T-007 esphome-driver | ESPHome native-API satellite driver: connection and capture with Silero segmentation (playback is T-014) | merged |
| T-008 stt | Streaming Polish STT on faster-whisper large-v3-turbo | merged |
| T-009 wake-matcher | Wake-phrase matcher ("Ryszardzie", vocative only) | merged |
| **T-013 wake-test-runner** | Minimal CLI: connect to the configured satellites, run VAD, STT and the wake matcher, print wake and final transcripts with timings, so Maciej can test "Ryszardzie" live on piotr. Runs right after T-007, T-008 and T-009 merge, before T-010. Throwaway: T-012's main is built on T-010's session, not on this loop. | merged |
| T-010 session | Per-satellite session state machine with the wake matcher (accept, cancel, reply timeout); follow-up and busy handling are P1 | merged |
| T-011 api | WebSocket API events only (commands such as speak/play/stop/release are P1) | merged |
| T-012 main-compose | Hub main wiring and compose with the hub only | merged |
| T-014 esphome-playback | ESPHome playback split from T-007: shared playback HTTP server, media_player announce, drain, PlaybackFinished | merged |

P0 is code-complete (T-014 merged as aa6f5ea). The hub on piotr still runs the T-012 build
(48b5f70) for Maciej's acceptance test; nothing is redeployed until he says so.

## P1 (design.md §11)

Exit criteria: on hardware, phrase → transcript with speaker → `speak` reply → follow-up answer
without the phrase. Order is the dependency order; `speak` comes first because it is the next
thing Maciej can hear on hardware (after T-017).

| Task | Scope | Depends on | Status |
| --- | --- | --- | --- |
| T-015 piper-service | The hub's own `wyoming-piper` compose service on the GPU (port 10201): an image with only `onnxruntime-gpu` 1.26.0 (no `onnxruntime`), the voice pinned by revision and hash, a CUDA-first provider check (no silent CPU fallback), HEURISTIC cuDNN algorithm search, warm-up before it listens; smoke test on piotr | T-003, T-012 | in review (PR #18) |
| T-016 tts-client | `tts/`: Wyoming Piper client (old `PiperTextToSpeech.synthesize`), streamed PCM chunks, a timeout on every wait, a text-length cap per call; warm-up at hub startup before arming | T-004, T-015 | planned |
| T-017 speak-release | API commands `speak` and `release` (`ack`/`error`) for a satellite in `PROCESSING`; session plays TTS through T-014 (`speaking` state, `playback_finished`, re-arm) with a bound on `PlaybackBegin` → `PlaybackEnd`; anything else is `busy` | T-014, T-016 | planned |
| T-018 speaker-hint | Speaker-recognition service in the compose file (port 2141) and the HTTP client (`speaker/`); `transcript_final` carries `speaker` | T-012; can run in parallel with T-015–T-017 | planned |
| T-019 announce-busy-stop | `speak` to an idle satellite (announcement: stop listening, play, re-arm), the full busy policy (design §6), and `stop` | T-017 | planned |
| T-020 follow-up | `listen_after`: `FOLLOW_UP_READY` cue, `follow_up_seconds` window without the phrase, `follow_up_timeout`, `follow_up` and `listening_for_follow_up` flags | T-019 | planned |
| T-021 play-url | `play` with a consumer URL: its own driver command, its end from the media player state (bounded), `listen_after` and `stop` as for `speak` | T-020 | planned |
| T-022 p1-acceptance | Deploy the three-service compose on piotr and run the P1 exit criteria on all four satellites (PR checklist only, no code unless a fix is needed) | T-015 … T-021 | planned |

Task notes (they go into each task's plan.md):
- **T-015 (Piper on the GPU):**
  - Image: install only `onnxruntime-gpu==1.26.0`. 1.28+ are CUDA 13 builds and silently fall
    back to CPU on the CUDA 12.8 image, and having `onnxruntime` installed too makes it run on
    CPU (design §3, §9).
  - Voice `pl_PL-bass-high`: download it at build time from a pinned Hugging Face revision
    (a commit id, not `main` as `tools/gpu-smoke.sh` uses) and check its SHA-256. No
    `--update-voices` at runtime.
  - No CPU fallback: the session must fail loudly when the CUDA EP is not active. **Decided
    (T-015, measured):** the guard is a check that `CUDAExecutionProvider` is the session's
    first provider; when the CUDA EP cannot load, onnxruntime silently runs everything on CPU
    and lists only the CPU EP. ORT's `session.disable_cpu_ep_fallback=1` (which
    `tools/gpu_smoke/ort_smoke.py` uses on its small test model) **cannot be used for this
    voice**: ORT always places its 678 shape ops (Unsqueeze, Gather, Concat, …) on CPU, next to
    2036 nodes on CUDA, so the session refuses to start. For the same reason it is not added
    to `tools/gpu_smoke/piper_vram.py`, whose provider-list check is the right one.
  - First inference: T-003 measured 48 s, then 0.1 s. **Measured in T-015:** it is not the
    cuDNN algorithm search (the same 48 s with HEURISTIC) but a one-time CUDA JIT compile for
    sm_120, cached in the container; a restart of the same container is warm in 2 s, a new
    container in about 50 s. The service sets `cudnn_conv_algo_search=HEURISTIC` in its own
    session (no patch of wyoming-piper needed) and warms up before it listens. The text cap
    per call (T-016) stays, to bound each call.
  - **Decided:** no host directory for the CUDA compile cache (simplicity; about 50 s once
    per new container is fine).
  - Own service, not a reuse of `piotr-wyoming-piper-1`. That container (checked read-only on
    2026-10-19) is the old project's: compose project `piotr` from
    `/home/maciek/piotr/docker-compose.yml`, image `piotr-wyoming-piper:latest` (built
    2026-06-13, CUDA base with cuDNN 9.8), `wyoming-piper --voice pl_PL-bass-high --use-cuda
    --update-voices --samples-per-chunk 1024` on host port 10200, GPU requested. Design §9
    says that image has both `onnxruntime` and `onnxruntime-gpu` and so runs on CPU, and its
    voice is not pinned (`--update-voices`). It is not ours to change or stop.
  - **Decided (ports):** our Piper listens on **10201** (`tts.uri`); our speaker recognition
    (T-018) on **2141** (`speaker_recognition.url`), next to the old project's 10200 and 2140.
    Both are configurable.
- **T-016 (warm-up):** **decided:** the Piper service warms itself before it listens (T-015),
  so the hub's startup step is only a reachability check: a Wyoming `describe` → `info` round
  trip, bounded, before any satellite is armed. A failure exits 1. The text cap: split long
  text at sentence ends into calls of at most N characters, so every call has a bounded
  length.
- **T-017 hardware checklist:** the first audible reply. Includes the T-014 playback-end
  comparison below.
- **T-018 (ports):** listen on 2141 (decided above); T-018 updates design.md §7, §9 and
  `config/hub.example.yaml` from 2140.
- **T-020 (follow-up cue):** **decided by Maciej (NARROW SCOPE):** T-020 uses only the cues
  the devices already have, with a documented substitute for the missing
  `play_follow_up_ready_cue`; the firmware import stays in P2. Checked in the old firmware
  (`firmware/esphome/packages/`, read only): neither device exposes the follow-up sound as an
  API service. BOX-3's `play_follow_up_listening_cue` is a script, and Voice PE's
  `follow_up_listening_sound` is a sound file; both are reached over the API only through
  `start_follow_up_listening`, which also starts the on-device follow-up listening that the
  hub does not use (P2 removes it), so T-020 must not call it. The cue services both devices
  expose are `play_message_end_cue` (the `accepted` cue) and `play_conversation_timeout_cue`.
  T-020's plan picks the substitute from these (or the `LISTENING` visual alone) and documents
  it; this is the T-020 design question.

## P2 (design.md §11), for reference

Not numbered yet: firmware trimmed to continuous mode and reflashed (including the cue
`api.respond` carry-forward below), recorded-audio fixtures, the audio-liveness check, then the
72 h soak test, docs.

## Carry-forwards

- **T-010 (done in T-010):** re-arm after `MicrophoneUnavailable` must back off (for example 1 s, doubling up to
  30 s, reset after a successful arm), with one log line per attempt. The driver itself never
  retries, and a refused connect fails in milliseconds (PR #9 review).
- **T-010 (done in T-010):** commands to one satellite are serialized: the session is the driver's only
  command sender and never sends a command while another is in flight (assert it in the
  session tests). The driver does not lock commands itself (PR #9 review).
- **Driver follow-up (done in T-010):** on disconnect, a pending start wait (`_stream_started`)
  or stop-ack wait (`_stream_stopped`) fails at once with the disconnect reason instead of
  running to its timeout and reporting a timeout (PR #9 review).
- **Driver follow-up (done in T-010):** a half-open connection is closed when the reader is
  cancelled mid-connect (`_connect` cancelled after `client.connect()` but before the client is
  published), so no orphaned API connection stays open (PR #9 review).
- **T-012 (done in T-012):** on shutdown, main must cancel each `SatelliteSession.run()` task, wait for it,
  then call `SatelliteSession.shutdown()` for all of them (stop listening, idle visual, close;
  bounded; `shutdown()` asserts `run()` is not running).
  Also raise the `aioesphomeapi` logger to ERROR: it logs its own WARNING per failed connect,
  next to the session's one (PR #12 review).
- **T-012 (done in T-012):** create the `ApiServer` (T-011) from `api.host`/`api.port`, pass its `publish` as
  every session's `emit`, start it before the sessions and close it after their `shutdown()`.
  Consider raising the `websockets.server` logger to WARNING: it logs each `/healthz`
  request at INFO ("connection rejected (200 OK)").
- **box3-office host (done in T-011):** its real mDNS name is `piotr-box3-01-cbfaA8.local`
  (old config, T-013 live run); `config/hub.example.yaml` and design.md §7 are fixed.
- **P2 (before the soak):** an audio-liveness check: while armed, no audio from the device
  for a bounded time is `MicrophoneUnavailable`, so a silent stall is not mistaken for a quiet
  room (PR #9 review, B1).
- **T-014 (driver side done in T-014):** a true `CueFinished`. The driver calls a cue service
  with `return_response` when the firmware declares `supports_response`, and is otherwise
  fire-and-forget as before.
- **P2 (firmware):** make each cue action answer once its cue has played. ESPHome sends a
  service response only from an explicit `api.respond` action: declaring `supports_response`
  alone makes every cue wait out the driver's 8 s and drop the connection (PR #16 review B2).
  Use status mode, and end the action with `api.respond` after `script.wait`:
  ```yaml
  api:
    actions:
      - action: play_message_end_cue
        supports_response: status
        then:
          - script.execute: play_message_end_cue
          - script.wait: play_message_end_cue
          - api.respond:
              success: true
  ```
  Do the same for `play_conversation_timeout_cue`, and for `play_follow_up_ready_cue` when it is
  added; then reflash. The driver calls status-mode actions with `return_response=False` and
  waits at most 8 s. Until then `CueFinished` follows the call, so the cue may still be playing
  (T-014 decision Q1).
- **P1 → T-017 (session, playback):** bound the time from `PlaybackBegin` to `PlaybackEnd` (TTS
  synthesis) in the session. Neither the driver nor the playback HTTP writer, which waits for
  the next chunk without a bound of its own, limits it; the drain's bounds start at
  `PlaybackEnd` (PR #16 review nit 1).
- **P1 → T-017 hardware checklist, re-checked in T-022 (hardware test):** compare T-014's
  playback-end estimate (the stream fully sent, plus the rest of the audio, plus 0.2 s, as the
  old driver did) with the media player's own `ANNOUNCING` → `IDLE` state, on Voice PE and
  BOX-3. Switch to the state only if the estimate cuts replies off or re-arms too early (T-014
  decision Q2).
- **P1 → T-021 (API commands):** `play` with a consumer URL (design §4.1 step 6) needs its own
  driver command and its end from the media player state; T-014 only streams PCM, which `speak`
  needs (T-014 decision Q3).
- **P1 → T-020 (follow-up):** the `play_follow_up_ready_cue` firmware service (BOX-3 already has
  `play_follow_up_listening_cue`, Voice PE `follow_up_listening_sound`) is needed when follow-up
  lands, ahead of the rest of the P2 firmware work. Until then the driver logs it as missing at
  connect and skips the cue with a warning. **Decided:** the firmware import stays in P2;
  T-020 uses a documented substitute from the cues the devices already have (T-020 note
  above).
- **P1 → T-017, T-019, T-020, T-021 (from T-010, T-011):** busy handling (T-019), follow-up
  (T-020) and the commands `speak`/`release` (T-017), `stop` (T-019) and `play` (T-021) were
  left out of P0.
- **P1 → T-017 (playback close at low rates):** the graceful close of a finished stream
  (`CLOSE_TIMEOUT_SECONDS`, 5 s, `hub/playback_http.py`) is tight at 8 kHz mono (16 KB/s):
  flushing a full 64 KiB asyncio write buffer takes about 4 s. If it fires, `abort()` drops the
  tail after the stream was already counted as complete. Piper's 22050 Hz (about 1.5 s) is
  safe; scale the limit with the rate (for example 64 KiB / byte rate + 2 s) if a producer
  uses a low rate (PR #16 re-review 5569307179). This is the close, not the 5 s request read
  (`REQUEST_READ_TIMEOUT_SECONDS`, `hub/playback_http.py`) or the driver's 5 s fetch wait
  (`STREAM_REQUEST_TIMEOUT_SECONDS`, `satellites/esphome.py`); neither depends on the rate.
