# T-021: play-url: plan

**Status:** in review (2026-10-30). Branch `t-021-play-url`, from `origin/main` (fa4316c,
T-020 merged as PR #29).

## Goal

> **T-021 play-url** (T-020): `play` with a consumer URL: its own driver command, its end from
> the media player state (bounded), `listen_after` and `stop` as for `speak`.

design.md §4.1 step 6 and §6 (`play` row, busy policy), tasks README note "P1 → T-021"
(T-014 decision Q3). Out of scope: volume, fetching or probing the URL in the hub, formats
(the device's business), any firmware change.

## Interpretations (where the docs leave a choice: the simplest reading)

1. **Busy policy as `speak`.** §6 already names `speak`/`play` together: a reply while
   `processing`, an announcement while `idle` with no open segment (stop listening, no cue,
   `IDLE` visual kept, status `speaking`), `busy` otherwise, also while anything plays.
2. **No volume.** The `play` row has `url` and `listen_after?` only; `volume` (or any other
   key) is `bad_request`. Nothing sets a volume.
3. **URL validation.** A string of 1 to 2048 characters, printable, no whitespace, scheme
   `http` or `https`, a host, and a valid port if any. Anything else is `bad_request`. The hub
   never fetches it.
4. **Driver command.** A new `PlaybackUrl(playback_id, url)`: a whole playback (no chunks, no
   `PlaybackEnd`), valid only while disarmed, `PlaybackStop` applies, exactly one
   `PlaybackFinished`. ESPHome: `media_player_command(media_url=url, announcement=True)` once
   the player is not busy (at most 3 s, as for a stream: a Voice PE drops an announcement URL
   sent during a cue, T-017), so the cue interplay is the stream's. `announcement=True` ducks
   other media like the hub's own replies.
5. **End from the player state** (README note, T-014 Q3), the cue rule: a player change after
   the call within 5 s, at least 0.5 s after it, then not busy (`ANNOUNCING`/`PLAYING`).
   Bounded at 10 min: past it the driver logs a WARNING, sends `STOP` and finishes.
6. **A URL the device never plays** (unreachable, 404, a format it cannot decode): no player
   change within 5 s → WARNING naming the playback, then one `PlaybackFinished`. The protocol
   has no reason field, so none is added; the satellite is not dropped (the connection is
   fine). The hub cannot tell it from a very short sound, so there is no timeout cue and
   `listen_after` still opens the window ("played and not stopped" means "ended without a
   `stop`" for `play`).
7. **stop.** Covers `play` exactly as `speak` (one `stop`, a second is `busy`). A `stop` before
   the URL went out sends nothing; after it, `PlaybackStop` and the driver's one
   `PlaybackFinished`. One `playback_finished` either way, `listening_for_follow_up: false`.
8. **Failures.** A failed media player command is the satellite failing
   (`MicrophoneUnavailable`, no `PlaybackFinished`); the session recovers with one
   `playback_finished` and no window, as for `speak`. The session bounds the whole play at
   720 s (above the driver's 10 min) as a satellite failure.
9. **Logs** show the URL without user info, query or fragment (they may carry tokens). The
   DEBUG command log keeps the full command, as for every command.
10. **Reachability.** The satellite fetches the URL itself, so it must be reachable from the
    satellite (its network, DNS, and a certificate it accepts for `https`), unlike the hub's
    own stream on 8751, which ufw on piotr opens to the satellites only. Documented in
    design.md §4.1 step 6 and §6.

## Design

- `satellites/messages.py`: `PlaybackUrl`; `protocol.py`: `DISARMED` → `PLAYING_AUDIO`, input
  complete at once.
- `satellites/esphome.py`: `_play_url`, `_follow_url` (start 5 s, floor 0.5 s, end 10 min),
  URL redaction in logs.
- `hub/session.py`: `play(url, listen_after)` beside `speak` through one `_take_output`
  (online check, reply/announcement/busy); `_play_url` sends, waits under `_until_stop`, sends
  `PlaybackStop` on a stop, then `_finish_playback`.
- `hub/commands.py`: `play` routed and validated; "not supported yet" gone.
- `tools/speak_test.py`: `--play URL` (reply, `--announce`, `--stop-after`, `--listen-after`).

## Tests

- Protocol table and a whole-playback test; command parsing (good and bad URLs, refusals,
  log line); session (reply, announcement, `listen_after`, stop at ack and while playing,
  stop as it finishes, busy, after a reply timeout, never finishes, disconnect); driver (end
  on idle, merged update after the floor, never plays, end bound, stop, stop after finished,
  waits out a cue, disconnect, failed command, log redaction); tool (`--play` combinations,
  reply, announcement, refusal, stop-after).

## Hardware checklist (Maciej, in the PR body)

voice-pe-02 (living_room) and voice-pe-03 (study): a reply `play`, an announcement `play`, a
`stop` mid-play, `listen_after`, an unfetchable URL (WARNING, one `playback_finished`). Test
audio: pinned raw.githubusercontent.com files (this repo's `tests/data/speech_pl_16k.wav`,
openai/whisper `tests/jfk.flac`, 11 s), https, which the satellite must reach.
