# T-020: follow-up listening: plan

**Status:** in review (2026-10-30). Branch `t-020-follow-up-listening`, from `origin/main`
(e60e03a, T-019 merged as PR #28).

## Goal

> **T-020 follow-up** (T-019): `listen_after`: `FOLLOW_UP_READY` cue, `follow_up_seconds`
> window without the phrase, `follow_up_timeout`, `follow_up` and `listening_for_follow_up`
> flags.

Narrowed by Maciej (README T-020 note, 2026-10-19): the `LISTENING` light only, no ready cue,
until the P2 firmware. design.md §4.1 step 7 otherwise as written. Out of scope: `play`
(T-021), the firmware cue (P2), any conversation state beyond the one flag.

## Interpretations (where the docs leave a choice: the simplest reading)

1. **Which speaks.** Step 7 says "if the command had `listen_after: true`": a reply and an
   announcement alike. An announcement with `listen_after` opens the window too.
2. **When no window.** Only after audio that played to its end (also the part produced before
   a TTS failure mid-stream: it played, and it may well end in the question). A `stop`ped speak, a reply
   with no audio (it gets the timeout cue, T-017), an announcement with no audio, or a
   satellite failure: `playback_finished.listening_for_follow_up: false`, plain idle re-arm.
3. **No ready cue.** After playback: `SetVisualState(LISTENING)`, `StartListening`. No
   `PlayCue` (Maciej, 2026-10-19), so no substitute cue either.
4. **Window length.** `follow_up_seconds` (config, default 8) from `ListeningStarted`. Speech
   must *start* before the deadline; the segment may end after it (as for the wake-word pause).
5. **API state.** `listening` while the window is open (design §6 `listening` widened to
   "a wake candidate or the follow-up window"). Busy: `speak` (no announcement taken),
   `release`, `stop` are all `busy`, like an open wake.
6. **The follow-up segment.** The whole text, less a leading wake word (`request_text`, the
   same rule as after the wake word alone). `wake` on the first partial with request text,
   or at the final; `transcript_partial`s with the segment's `utterance_id`;
   `transcript_final.follow_up: true`. The accept is the normal one (accepted cue,
   `PROCESSING`, speaker hint, reply wait).
7. **Timeout.** No speech by the deadline: `follow_up_timeout`, `StopListening`
   (`follow_up_timeout`), the timeout cue (step 7 says so; the "no sound" decision is about the
   ready cue only), `IDLE`, re-arm.
8. **A segment with no request text** (noise, music, "..."): ends the window like the timeout
   (the bare-wake precedent, T-017 fix): a `wake` a partial sent gets `ResetWakeCandidate` and
   `wake_cancelled` first, then `follow_up_timeout`, stop, timeout cue, idle re-arm.
9. **The wake word alone in the window** ("Ryszardzie", a pause, the request): the ordinary
   wake-word-alone path; that request is addressed by name, so `follow_up: false`.
10. **Disconnect in the window.** The window is dropped, no `follow_up_timeout` (the `error`
    status says it); an open `wake` is cancelled as for any failure; back with a plain idle arm.
11. **Chains.** A follow-up answered with another `listen_after` speak opens another window.
    No other state is kept. Shutdown emits nothing.

## Design

- `commands.py`: `listen_after` (bool, default false) passes through to `Speaker.speak`; the
  "not supported yet" refusal goes. Non-bool stays `bad_request`.
- `session.py`: `_Speak.listen_after`; `_speak` sets `_follow_up_next` when the audio began
  and was not stopped; `_finish_playback` reports it as `listening_for_follow_up`; `_arm`
  consumes it: `LISTENING` visual, start, then a deadline and status `listening`.
  `_listen_until_accepted` waits for the next event until the deadline; `SpeechStarted`
  clears the deadline and marks the segment `follow_up`. `_end_follow_up` is the timeout path.
  `_recover` drops both fields. `run()` stays the only command sender; no new task.
- `events.py`/`api.py`: `FollowUpTimeout(satellite)` → `follow_up_timeout`, data `{}`.
- `tools/speak_test.py`: `--listen-after` adds `"listen_after": true` to every speak (reply
  and `--announce`); refused with `--stop` and `--release`.

## Tests

- Session: window opens with `LISTENING` and no cue; a follow-up without the phrase (and with
  a leading phrase dropped) → `transcript_final` `follow_up: true`; an announcement with
  `listen_after`; timeout (`follow_up_timeout`, stop reason, timeout cue, idle, not before
  `follow_up_seconds`); a segment with no request text, with and without a partial `wake`;
  a follow-up ending after the deadline; the wake word alone in the window; no window after
  `stop` or no audio; busy in the window and while capturing the follow-up, an announcement
  taken after; disconnect in the window and during follow-up speech; disconnect during a
  `listen_after` speak; a chained second window.
- Commands/API: `listen_after` passed through, non-bool refused, `follow_up_timeout` envelope,
  `playback_finished.listening_for_follow_up: true`.
- Tool: `--listen-after` on replies, on follow-ups, on `--announce`; bad combinations.

## Checklist

- [x] plan.md, log.md
- [x] commands, session, events, API
- [x] tests; `make check` green
- [x] tools/speak_test.py `--listen-after`
- [x] docs: design §4.1 step 7 and §6 (incl. PR #28 re-review N2), microphone-protocol.md,
      tasks README (T-019 merged, T-020 in review), T-019 plan status (N1)
- [ ] hardware checklist (Maciej, voice-pe-02 and voice-pe-03)
