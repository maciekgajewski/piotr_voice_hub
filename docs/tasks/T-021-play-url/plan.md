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
6. **A URL the device cannot play** (unreachable, 404, a refused port, a format it cannot
   decode) ends in one `PlaybackFinished` by one of two paths (PR #30 review S1): the firmware
   may announce, fail and go idle at once, which ends like a very short sound (INFO
   `playback finished … (url) after 0.5 s`, the seconds show it), or the player reports no
   change within 5 s (a WARNING naming the playback; a slow https start may land here too and
   then play after the hub has moved on). Which one a Voice PE takes is recorded on hardware.
   The driver can tell the second path, but the protocol has no reason field (none added), so
   the session treats both as played (review N4): no timeout cue (a failed reply is the
   accepted cue, then silence) and `listen_after` still opens the window ("played and not
   stopped" means "ended without a `stop`" for `play`). The satellite is not dropped.
7. **stop.** Covers `play` exactly as `speak` (one `stop`, a second is `busy`). A `stop` before
   the URL went out sends nothing; after it, `PlaybackStop` and the driver's one
   `PlaybackFinished`. One `playback_finished` either way, `listening_for_follow_up: false`.
8. **Failures.** A failed media player command is the satellite failing
   (`MicrophoneUnavailable`, no `PlaybackFinished`); the session recovers with one
   `playback_finished` and no window, as for `speak`. The session bounds the whole play at
   720 s (above the driver's worst case, 3 + 5 + 0.5 + 600 + 3 s; a test pins it) as a
   satellite failure.
8a. **A play must not outlive the hub** (PR #30 review B). A URL, unlike the hub's stream,
   does not end when the driver forgets it. Every path that ends a play while the device may
   still play goes through the driver's `close()` or a reconnect: a lost API connection or a
   driver failure (the session recovers: `close()`, then a reconnect), the session's 720 s
   give-up (the same), and a hub shutdown or restart (`shutdown()` calls `close()`; the new
   hub connects). So: `close()` sends the media player `STOP` if a URL is current (best
   effort; a failure is a WARNING), and every connect, before anything uses the device,
   stops a busy player (nothing else plays media on these satellites) and waits for it to go
   idle, at most 3 s (WARNING with the state; still busy after 3 s is a WARNING and goes on).
   A dropped consumer (API websocket) ends nothing: the play runs to its end (bounded) and
   `playback_finished` goes to the clients still connected. The driver is published as ready
   only after the check, so neither the reader nor a command uses the device before it.
8b. **Wake phrase.** A `play` keeps the satellite deaf for its whole length (half-duplex,
   MP-AUDIO-001), up to 10 min; only `stop` ends it. Documented, not changed (review N6).
9. **Logs** show the URL without user info, query or fragment (they may carry tokens).
   `PlaybackUrl.url` is `field(repr=False)`, so the DEBUG command log and assertion texts show
   only the playback id (review N5). A token in the path (`/t/<token>/a.mp3`) still reaches
   the INFO line; accepted.
10. **Reachability.** The satellite fetches the URL itself, so it must be reachable from the
    satellite (its network, DNS, and a certificate it accepts for `https`), unlike the hub's
    own stream on 8751, which ufw on piotr opens to the satellites only. Documented in
    design.md §4.1 step 6 and §6.

## Design

- `satellites/messages.py`: `PlaybackUrl`; `protocol.py`: `DISARMED` → `PLAYING_AUDIO`, input
  complete at once.
- `satellites/esphome.py`: `_play_url`, `_follow_url` (start 5 s, floor 0.5 s, end 10 min,
  the INFO end line with its seconds), URL redaction in logs; `close()` stops a current URL;
  `_stop_stray_player` on connect, and `_ready` so callers wait for it.
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
- PR #30 review: the stray play at connect (stopped before the first arm; bounded; idle left
  alone; no state; a failed stop; a concurrent command waits), `close()` stopping a URL (and
  not otherwise; a failed stop), the wait after the STOP at the 10-min cap, no ERROR on a
  disconnect mid-URL, the 720 s bound against the driver's sum, 2048/2049, the elapsed
  seconds in the INFO line, no URL in DEBUG lines or the repr.

## Hardware checklist (Maciej, in the PR body)

voice-pe-02 (living_room) and voice-pe-03 (study). Main source a LAN http server on Maciej's
laptop (`python3 -m http.server`, no change to piotr's firewall) with files fetched from pinned
raw.githubusercontent.com URLs and a generated 0.3 s beep; one https step. Cases: reply,
announcement, `stop` mid-play, `listen_after`, busy, a LAN 404, a refused port, a clip under
0.5 s, an endless internet radio stopped with `--stop-after`, a hub restart while the radio
plays (review B), and (step 11b) a `docker kill` + `docker start` while the radio plays: SIGKILL
skips `close()`, so only the connect check can stop the radio; record its two log lines with
their timestamps and the seconds from `docker start` to silence. Each failure case records
which log line appeared (review S1).
