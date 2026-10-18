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
| T-007 esphome-driver | ESPHome native-API satellite driver with Silero segmentation | in progress |
| T-008 stt | Streaming Polish STT on faster-whisper large-v3-turbo | PR #8 |
| T-009 wake-matcher | Wake-phrase matcher ("Ryszardzie", vocative only) | PR #7 |
| **T-013 wake-test-runner** | Minimal CLI: connect to the configured satellites, run VAD, STT and the wake matcher, print wake and final transcripts with timings, so Maciej can test "Ryszardzie" live on piotr. Runs right after T-007, T-008 and T-009 merge, before T-010. Its loop is the seed of T-012's main. | planned |
| T-010 session | Per-satellite session state machine (accept, cancel, reply timeout, follow-up, busy) | planned |
| T-011 api | WebSocket API events and commands | planned |
| T-012 main-compose | Hub main wiring and compose with the hub only | planned |
