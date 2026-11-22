# T-029: audio-liveness check

**Status:** planned. Branch `t-029-audio-liveness`.

## Goal

A satellite that is connected but streaming silence, or that stops sending audio while still
armed, is reported as `MicrophoneUnavailable` within a bound, not treated as a quiet room
(PR #9 review B1; design reliability: a stall is a failure).

## Scope

- In the ESPHome driver (or session), while armed, if no audio frames arrive for a bounded
  time, raise `MicrophoneUnavailable` with a clear reason. Existing reconnect/backoff
  (T-010) then applies.
- Choose the bound so a genuinely quiet room (VAD seeing silence in real frames) is not
  flagged; only a missing or dead stream is.
- Tests with a fake transport: frames then a stall; a quiet-but-alive stream of silence
  frames; a stall that recovers before the bound.
- A short note in design.md §4.1 or the reliability list, and the bound in config if it
  needs to be tuned, otherwise a named constant.

## Out of scope

- The 72 h soak (T-030), firmware changes (T-027), and any live fault injection on a real
  satellite in this PR.
- Changing `/healthz` (that endpoint already reports 200 while the hub serves, even with
  satellites offline).

## Approach

The driver already times out device awaits and raises `MicrophoneUnavailable`. Add a
per-stream idle timer that resets on every audio chunk and fires only while listening is
started. Do not use VAD speech probability: a quiet room still produces frames. Keep the
session as the only command sender; the reader raises, and the session recovers as today.

## Tests

- Armed, frames stop: `MicrophoneUnavailable` after the bound, not before.
- Armed, silence frames keep arriving: no unavailable, VAD may emit nothing.
- Disarmed / stopped: the timer is not running.
- After unavailable, the existing backoff/re-arm path still runs (one existing session test
  extended, not a new retry policy).

## Manual (hardware) checklist outline

- [ ] `make check` is green. No satellite is disconnected on purpose in this PR.
- [ ] Later live check (with the manager's go): confirm a quiet room is not flagged, and
      that a real drop (satellite unplugged or stream stopped) becomes unavailable and
      recovers. Restore device state afterwards.

## Risks

- A bound that is too tight will flap on jitter and look like reconnect storms in the soak.
  Prefer a conservative bound (seconds, not hundreds of milliseconds) and make it obvious
  in the log line.
- Do not confuse "no VAD speech" with "no audio frames".

## Dependencies

- T-007 / T-010 (driver and session recovery). Must land before T-030.
- Independent of T-027 and T-028.
