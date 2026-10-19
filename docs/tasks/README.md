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
| T-010 session | Per-satellite session state machine with the wake matcher (accept, cancel, reply timeout); follow-up and busy handling are P1 | in progress |
| T-011 api | WebSocket API events only (commands such as speak/play/stop/release are P1) | planned |
| T-012 main-compose | Hub main wiring and compose with the hub only | planned |
| T-014 esphome-playback | ESPHome playback split from T-007: shared playback HTTP server, media_player announce, drain, PlaybackFinished | planned |

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
- **T-012:** on shutdown, main cancels each `SatelliteSession.run()` and then awaits
  `SatelliteSession.shutdown()` for all of them (stop listening, idle visual, close; bounded).
  Also raise the `aioesphomeapi` logger to ERROR: it logs its own WARNING per failed connect,
  next to the session's one (PR #12 review).
- **P2 (soak):** an audio-liveness check before the soak: while armed, no audio from the device
  for a bounded time is `MicrophoneUnavailable`, so a silent stall is not mistaken for a quiet
  room (PR #9 review, B1).
- **T-014:** a true `CueFinished`. Make the firmware cue services `supports_response` (they
  already `script.wait`) and call `execute_service(..., return_response=True, timeout=...)`.
  Today `CueFinished` follows a fire-and-forget call, so the cue may still be playing.
- **P1 (follow-up):** the `play_follow_up_ready_cue` firmware service (BOX-3 already has
  `play_follow_up_listening_cue`, Voice PE `follow_up_listening_sound`) is needed when follow-up
  lands, ahead of the rest of the P2 firmware work. Until then the driver logs it as missing at
  connect and skips the cue with a warning.
