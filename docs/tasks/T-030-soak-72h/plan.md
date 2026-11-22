# T-030: 72 h soak

**Status:** planned. Branch `t-030-soak-72h`.

## Goal

A 72-hour soak on all configured satellites with a TV on, plus a log-summary tool, so false
accepts, reconnects and stuck states are counted (design.md §10–§11).

## Scope

- A written procedure: start and stop, which satellites, TV on, what Maciej should (not) do,
  how to abort, and what "pass" means.
- A log-summary tool that reads hub container logs and counts false accepts (wake without the
  phrase), reconnects, `MicrophoneUnavailable`, stuck states (armed with no audio, session
  states that never leave PROCESSING, cue or playback waits that hit their bound), and
  reply/timeout events.
- If T-022 has not already measured them, include the wake-miss rate and false-wake count
  over a fixed utterance set and a quiet period (README carry-forward).
- The tool is read-only on logs. It does not connect to satellites or change production.

## Out of scope

- Starting the soak in the code PR. The run is a live session with Maciej, after T-027 is
  flashed and T-029 has landed. Production deploy still needs Maciej's own words.
- New firmware or liveness behaviour (those tasks already landed).
- D-capture / T-023 (stays off unless separately approved).

## Approach

Write the procedure and a small script (the smallest rerunnable check; no framework) that
parses the hub's JSON-file logs for the known INFO/WARNING lines. Prove it on a saved log
excerpt from an earlier hardware session, committed as a test fixture (no raw household
audio). The live 72 h run and the recorded counts go in the task log after the session.

## Tests

- The summary tool, given a fixture log, returns the expected counts (accepts, reconnects,
  unavailable, timeouts, stuck-state warnings).
- It exits non-zero on a malformed log path and never requires a live API connection.

## Manual (hardware) checklist outline

- [ ] Procedure and tool reviewed; tool checked against a fixture log.
- [ ] Preconditions: T-027 firmware is on the satellites, T-029 is deployed, D-capture is
      off, production deploy (if any) was approved in Maciej's own words.
- [ ] Start: note SHA, container IDs, volume, TV state; restore anything changed at the end.
- [ ] 72 h with the TV on; abort path documented.
- [ ] After: run the summary tool, record counts, compare to the T-022 baseline if it exists.

## Risks

- A soak started on unflashed or pre-liveness firmware will not test P2. Gate the run on
  T-027 and T-029.
- Log rotation (5 × 10 MB) can drop early events; the procedure should snapshot or copy
  logs during the run.
- False accepts need a way to tell a real wake from a TV false accept; the summary tool
  flags candidates, and Maciej confirms.

## Dependencies

- T-027 (firmware reflashed) and T-029 (liveness). T-028 is useful, not a hard gate.
