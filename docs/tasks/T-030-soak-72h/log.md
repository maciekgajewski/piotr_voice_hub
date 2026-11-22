# T-030 log

## 2026-11-22

- Numbered in the P2 breakdown. The live 72 h run waits for T-027 to be flashed, T-029 to
  land, and a session with Maciej. The code PR ships only the procedure and the log-summary
  tool.

- Plan: add a quiet-room check on Voice PE with auto_gain at 0 dBFS. A quiet room with no
  speech must not trip T-029 liveness (15 s, RMS < 16 → `MicrophoneUnavailable`); count any
  liveness trips in the logs.
