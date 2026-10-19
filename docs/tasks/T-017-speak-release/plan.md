# T-017: speak and release: plan

**Status:** planned (2026-10-19). Branch `t-017-speak-release` from `origin/main` (0fac79f).
It does not need PR #20 (in review): `TtsError` from main is enough here.

## Scope (docs/tasks/README.md, design.md §4.1 steps 5-6, §6)

> **T-017 speak-release** (T-014, T-016): API commands `speak` and `release` (`ack`/`error`)
> for a satellite in `PROCESSING`; session plays TTS through T-014 (`speaking` state,
> `playback_finished`, re-arm) with a bound on `PlaybackBegin` → `PlaybackEnd`; anything else
> is `busy`.

Carry-forwards for T-017 (README): the `PlaybackBegin` → `PlaybackEnd` bound, which is also the
overall per-synthesis bound (T-016 plan, N1); the playback HTTP close timeout scaled by the
sample rate; the hardware checklist with the playback-end comparison (T-014 Q2).

Out of scope: `listen_after` follow-up (T-020), `speak` to an idle satellite, the rest of the
busy policy and `stop` (T-019), `play` (T-021), the speaker hint (T-018).

## Design

- **API (`hub/api.py`, new `hub/commands.py`).** Client messages are now commands:
  `{"type", "id", "satellite", "data"}`. The reply goes to that client only, in its queue
  (so a slow client is dropped as before): `{"type": "ack", "reply_to", "satellite", "data"}`
  or `{"type": "error", "reply_to", "satellite", "data": {"code", "message"}}` (no `ts`, as in
  the design's example). Codes: `bad_request` (not JSON, not an object, missing or wrong
  fields, unknown keys, empty or over-1000-character text, a volume outside 0–1),
  `unknown_satellite`, `satellite_offline`, `busy`. `play`, `stop` and `listen_after: true`
  are `bad_request` "not supported yet" until T-019/T-020/T-021. One INFO line per command
  (type, id, satellite, outcome, text length; never the text).
- **Session (`hub/session.py`).** `speak(text, volume) -> playback_id` and `release()` are
  synchronous, called from the API task: they check the state and hand the request to the
  session's task through a one-shot future, so there is no queue and no race (one event
  loop). Accepted only while waiting for a reply (`PROCESSING`); offline is
  `satellite_offline`; any other state is `busy`. While waiting for a reply the session waits
  for the reply, a driver event (a disconnect) or `reply_timeout`, whichever comes first.
  - `release`: `ack`, then re-arm (no cue).
  - `speak`: `ack` with a new `playback_id`; state `speaking`; open the synthesis (T-016
    client); `PlaybackBegin` with its format and the volume (the command's, else
    `tts.volume`); a `PlaybackChunk` per PCM chunk; `PlaybackEnd`; wait for the driver's
    `PlaybackFinished`; emit `playback_finished` (`listening_for_follow_up: false`); re-arm.
    The visual stays `PROCESSING` while speaking (there is no speaking visual).
  - **Bounds:** `PlaybackBegin` → `PlaybackEnd` at most 30 s
    (`PLAYBACK_PRODUCE_TIMEOUT_SECONDS`; 1000 characters take about 1 s). This is also the
    per-synthesis bound: before it, the client's connect (5 s) and first-audio (10 s) bounds;
    within it, the next-chunk bound (10 s). The wait for `PlaybackFinished` is bounded at
    2 × the audio length + 60 s, above the driver's own drain bounds; past it the driver is
    treated as unavailable.
  - **Failures:** a `TtsError` before the first audio (Piper down, slow, refused): no
    playback, an ERROR line. A `TtsError` or the 30 s bound mid-stream: `PlaybackEnd` for
    what was produced (the satellite plays it), an ERROR line. A driver failure: the session
    recovers as today. **In every case, while the hub runs, each `ack`ed `speak` ends with
    exactly one `playback_finished`** (like every `wake` ends with `transcript_final` or
    `wake_cancelled`), so a consumer is never left waiting; the log says what failed.
- **Events:** `SatelliteState.SPEAKING` (`speaking`); hub event `PlaybackFinished(satellite,
  playback_id, listening_for_follow_up)` → `playback_finished`.
- **Playback HTTP close (`hub/playback_http.py`):** the graceful close of a stream waits
  `max(5 s, 64 KiB / byte rate + 2 s)` (6.1 s at 8 kHz mono, 5 s at Piper's 22050 Hz).
- **Main:** sessions get the TTS client; the API gets the command router.

## Tests

- `tests/test_commands.py`: parsing and validation, every error code, routing, replies.
- `tests/test_api.py`: a command over a real WebSocket gets its reply on that connection only;
  other clients get only events.
- `tests/test_session.py` (fake driver, fake TTS): speak (commands in order, format, volume,
  chunks, `playback_finished`, states, re-arm); release; busy/offline/bad text; TTS failure
  before and during audio; the produce bound; the `PlaybackFinished` bound; a disconnect
  mid-speak; reply timeout still works; a speak after the timeout is `busy`.
- `tests/test_playback.py`: the scaled close timeout.
- GPU-gated `tests/test_speak_gpu.py`: API + session + fake driver + our Piper on a test port:
  `transcript_final` → `speak` → `ack` → PCM at 22050 Hz → `playback_finished`.

## Manual test (Maciej, hardware)

In the PR: speak a reply on voice-pe-02/03 from `python -m websockets` in the hub container,
then `release`; plus the T-014 playback-end comparison (the estimate vs. the media player's
`ANNOUNCING` → `IDLE`).
