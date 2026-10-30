# T-019: announcements, the busy policy and stop: plan

**Status:** in progress (2026-10-30). Branch `t-019-announce-busy-stop`, from `origin/main`
(ac36f1f, T-018 merged).

## Scope (docs/tasks/README.md, design.md §4.1 step 8, §6)

> **T-019 announce-busy-stop** (T-017): `speak` to an idle satellite (announcement: stop
> listening, play, re-arm), the full busy policy (design §6), and `stop`.

Carry-forward (README, from T-010/T-011/T-017): `stop` was refused as `bad_request` "not
supported yet" until this task. `play` (T-021) and `listen_after: true` (T-020) stay refused.

Out of scope: follow-up (T-020), `play` (T-021), barge-in (design §1: stopping by voice is out
of scope; `stop` is an API command only), a queue (design §6: none).

## Design (decided from the docs)

- **Announcement (design §4.1 step 8).** `speak` is an announcement when the satellite is
  `idle`: armed, no wake open, no speech segment open. The session takes it only while it is
  waiting for the next driver event in that state (like the reply wait takes `speak` and
  `release`), through a one-shot future, so `run()` stays the only command sender.
  Sequence: state `speaking`; `StopListening(reason="announcement")`; the playback exactly as
  for a reply (T-017: `PlaybackBegin` with the first chunk, chunks, `PlaybackEnd`,
  `PlaybackFinished`); `playback_finished`; re-arm (`IDLE` visual, start listening).
  - No cue before it (design: "stops listening, plays, then re-arms").
  - The visual is not changed: there is no speaking visual, and `PROCESSING` (thinking about
    a request) would be wrong for an announcement nobody asked for.
  - TTS failing before any audio: an ERROR line and no timeout cue. The cue exists so a person
    who was just given the accepted cue does not hear only silence (design §6); nobody waits
    for an announcement. Still exactly one `playback_finished`.
  - Race: the ack is given on the state the API sees. A `SpeechStarted` read in the same loop
    step as the ack is dropped by the stop (MP-STOP-002), with an INFO line; the announcement
    plays.
- **`listening` (a wake open) is busy.** design §4.1 step 8 says "an idle satellite" and the
  API state `listening` means a wake candidate is open: someone is addressing the hub. This
  includes the wake-word-alone pause (no segment open, `wake` sent).
- **The busy policy (design §6), in full.**

  | Satellite | `speak` | `release` | `stop` |
  | --- | --- | --- | --- |
  | offline (`error`) | `satellite_offline` | `satellite_offline` | `satellite_offline` |
  | `idle`, no segment open | ack: announcement | `busy` | `busy` |
  | `idle`, a segment open (speech without the wake phrase so far) | `busy` | `busy` | `busy` |
  | `listening` (a wake open, with or without a segment) | `busy` | `busy` | `busy` |
  | accepting (stop, cue, speaker hint; still `listening`) | `busy` | `busy` | `busy` |
  | `processing`, waiting for a reply | ack: reply | ack | `busy` |
  | a speak acked and not yet finished (`speaking`, incl. its synthesis and drain) | `busy` | `busy` | ack |
  | timeout cue, re-arming | `busy` | `busy` | `busy` |

  `stop` with nothing playing is `busy`, as `release` outside `processing` already is (T-017):
  the codes are `busy`, `unknown_satellite`, `satellite_offline`, `bad_request`, and
  `bad_request` is for malformed commands. A second `stop` for the same playback is `busy`.
  Error messages name the state ("is capturing speech", "is listening", "nothing is playing").
- **`stop` (design §6: "Stop current playback; re-arm").** Accepted from a speak's `ack` until
  its `playback_finished` (reply or announcement). `ack` data `{}`. It ends the speak at once:
  - before any audio: the synthesis is closed, nothing is played, no cue;
  - with audio sent: the synthesis is closed and a new driver command
    `PlaybackStop(playback_id)` stops the device;
  - then exactly one `playback_finished` (`listening_for_follow_up: false`) and re-arm, no cue.
  The session only interrupts its own waits (the TTS reads and the wait for
  `PlaybackFinished`), never a driver command in flight: `stop()` expires the asyncio timeout
  of the current wait at once (`Timeout.reschedule`); a stop that comes during a command is
  seen at the next wait.
- **Driver (`PlaybackStop`).** Valid in `PLAYING_AUDIO` for the current playback, before or
  after `PlaybackEnd`, once. The ESPHome driver sends
  `media_player_command(command=STOP, announcement=True)` (both firmware media players, BOX-3
  `speaker` and Voice PE `speaker_source`, stop their announcement pipeline for that; checked
  in the ESPHome source), releases the stream (its GET ends, a later GET is 404), waits for the
  player not to be busy (at most 3 s, as before a URL; a WARNING past it) and emits
  `PlaybackFinished`. If the drain already emitted `PlaybackFinished`, it does nothing: the
  session still gets exactly one. docs/microphone-protocol.md gets the command and the
  transitions.
- **API (`hub/commands.py`).** `stop` takes no data (like `release`); it is no longer "not
  supported yet".

## Steps

1. plan.md, log.md.
2. Protocol + messages: `PlaybackStop`; protocol tests.
3. ESPHome driver: `PlaybackStop`; driver tests with the fake client and the real playback
   HTTP server.
4. Session: announcement wait, `stop()`, interruptible waits, busy messages; session tests.
5. Commands: `stop`; command and API tests.
6. design.md §4.1 step 8 and §6, microphone-protocol.md, tasks README (T-018 merged, T-019 in
   review), `tools/speak_test.py` if it helps the hardware check.
7. `make check`, commit, push.

## Tests

- Session (fake driver, TTS): an announcement from idle (commands, events, re-arm, no cue,
  visual untouched); TTS failing before audio in an announcement (no cue, one
  `playback_finished`); speak busy while a segment is open, while a wake is open (also the
  wake-word-alone pause), while accepting and while speaking; the same-step race with a
  `SpeechStarted`; a disconnect during an announcement; stop before audio, mid-stream and
  during the drain (also of an announcement); stop when nothing plays and a second stop are
  busy; stop and a natural finish in the same step give one `playback_finished`.
- Protocol: `PlaybackStop` before and after `PlaybackEnd`, then `PlaybackFinished`; a
  `PlaybackStop` outside playback, mismatched or twice fails.
- Driver: stop mid-stream (STOP command sent, stream released, `PlaybackFinished`, the player
  wait); stop after the drain finished (no second `PlaybackFinished`); stop while unavailable.
- Commands/API: `stop` acks, takes no data, its errors.

## Acceptance

`make check` green; the hardware checklist in the PR (an announcement on an idle satellite,
busy while talking, `stop` of a long reply and of an announcement).
