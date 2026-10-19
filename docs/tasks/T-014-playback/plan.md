# T-014: ESPHome playback: plan

**Status:** Q1–Q3 decided by Maciej on 2026-10-19 (see Decisions); implemented as proposed
below, with unit tests only.

## Decisions (2026-10-19)

- **Q1: driver side only.** The driver uses the service response when the device declares
  `supports_response`; otherwise it behaves as today. The firmware change goes into the P2
  firmware task (a carry-forward in docs/tasks/README.md).
- **Q2: the stream-timing estimate,** as in the old project: all audio sent, plus the
  remaining duration, plus 0.2 s. A P1 hardware-test carry-forward compares it with the media
  player's announcing → idle state.
- **Q3: `play` with a consumer URL goes to P1** with the API commands. T-014 covers only the
  streamed playback that `speak` needs (a README note).

## Scope (docs/tasks/README.md)

- The shared playback HTTP server, `media_player_command(media_url, announcement=True)`, drain,
  and `PlaybackFinished` (split from T-007).
- Carry-forward: a true `CueFinished` through `supports_response` cue services.
- Only the driver side. The session's `speaking` state and the `speak`/`play`/`stop` API
  commands are P1. Piper/TTS is P1.

## What exists

**This repo**
- `satellites/messages.py` already has `PlaybackBegin(playback_id, rate, width, channels,
  volume)`, `PlaybackChunk`, `PlaybackEnd` and `PlaybackFinished`, from the old repo.
- `satellites/protocol.py` already checks their order. `PlaybackFinished` may only come after
  `PlaybackEnd`.
- `satellites/esphome.py` raises `NotImplementedError` for all three commands. Its `PlayCue`
  emits `CueFinished` right after a fire-and-forget service call.
- Config: `playback_http.port` (8751, can be overridden from the CLI). Design §9 lists 8751 as
  "Playback HTTP".
- **There is no firmware in this repo yet.** Design §8 copies and trims it in P2.

**Old repo (read-only, `piotr_the_ai_server` @ a299643)**
- The driver serves PCM chunks as a live WAV stream: one `ThreadingHTTPServer` per playback on
  a random port, path `/tts.wav`.
  - The header is an "endless" WAV header (data size `0x7FFFFFF0`). The PCM chunks follow as
    they arrive.
  - The URL host is the local IP of the route to the satellite (UDP-connect trick on the
    resolved address).
  - The media player key is the first `MediaPlayerInfo` entity.
  - It sends a separate volume command first, if one was given.
- Drain: wait for the device's GET (5 s), then for the whole stream to be sent (30 s), then
  sleep the rest of the audio length (counted from the first byte sent) plus 0.2 s. Then it
  emits `PlaybackFinished`.

**Firmware in the old repo** (`firmware/esphome/`; it is what runs on the devices today)
- Media players exposed over the API:
  - Voice PE: `external_media_player` ("Media Player", `speaker_source`; announcement pipeline
    FLAC 48 kHz mono). The `sendspin_group_media_player` has no name, so it should be
    internal; this needs checking on hardware.
  - BOX-3: `speaker_media_player` (`speaker`; announcement pipeline FLAC 48 kHz mono).
- API services (`packages/piotr-voice-satellite-api-services.yaml`):
  - `play_message_end_cue` and `play_conversation_timeout_cue`. Both `script.execute` and then
    `script.wait`, but neither has `supports_response`.
  - `start_open_mic_listening`, `stop_listening`, `reset_open_mic_wake_candidate`,
    `set_visual_idle/listening/processing`, and `start_follow_up_listening` (to be removed in
    P2).
  - `play_follow_up_ready_cue` does not exist yet (P1/P2).
  - No service is involved in playback; it goes through the media player entity.
- `min_version: 2026.3.0`.

**aioesphomeapi 45.3.1 (locked)**
- `execute_service(service, data, *, return_response=None, timeout=30.0)`.
- `UserService.supports_response` (NONE/OPTIONAL/ONLY/STATUS).
- `media_player_command(key, media_url=, announcement=, volume=)`.
- `MediaPlayerEntityState.state` includes `ANNOUNCING` and `IDLE`.

## URL vs streaming: not ambiguous

Design §4 (diagram), §4.1 step 6 and §9 say the same thing: the hub serves `/<playback_id>.wav`
from one shared HTTP server, and the satellite fetches it after
`media_player_command(media_url, announcement=True)`. The old project also plays through a URL
the satellite fetches. Its "streaming" is the driver's command interface (the hub hands PCM
chunks to the driver), which this repo already has in `messages.py`/`protocol.py`. That stays.
The one change from the old repo: one shared server on `playback_http.port` with a path per
playback, instead of one server per playback on a random port.

## Implementation

1. **`hub/playback_http.py`: one server, asyncio only (`asyncio.start_server`, no new
   dependency).**
   - `run_hub` creates it on `0.0.0.0:<playback_http.port>` (host networking, §9). It is
     started before everything else and closed after everything else; a failed bind exits 1.
   - `open_stream(playback_id, rate, width, channels, client_ip)` returns a `PlaybackStream`
     holding at most `MAX_STREAM_SECONDS` (300 s) of audio; more is `StreamFull`.
     - Formats: 16-bit PCM, 1 or 2 channels, at 8000, 11025, 16000, 22050, 24000, 32000, 44100
       or 48000 Hz. Anything else is a `ValueError` before anything is sent. It is not caught:
       a bad format is a producer bug, so it takes the session (and the hub) down on purpose.
     - `GET /<playback_id>.wav` gets the endless WAV header (the old driver's), then the chunks
       as they arrive, until the stream ends or is released.
   - One GET per stream, and only from the satellite's IP. An unknown, released or already
     fetched id, a non-GET, or a request from another IP gets 404 (another IP is told nothing
     more than for an unknown id). At most 8 connections at once from other addresses; more
     are aborted at once. A satellite with an open stream is never refused.
   - Bounds: reading the request takes at most 5 s, closing at most 5 s (then abort).
     - The 5 s close is tight at 8 kHz mono: flushing a full 64 KiB write buffer takes about
       4 s, and an abort then drops the tail of a stream already counted as complete. Piper's
       22050 Hz is safe; scaling it with the rate is a P1 carry-forward (T-017,
       docs/tasks/README.md; PR #16 re-review 5569307179, nit 2).
     - Audio is written in slices of at most 16 KiB, each with its own 10 s write limit (write
       plus drain). One limit for a whole chunk would cut off a healthy device for any chunk
       over about 10 s of audio (PR #16 re-review B1).
     - The real wait per slice is set by asyncio's write-buffer watermarks, not by the slice
       size: once the buffer passes 64 KiB, `drain()` waits until it is below 16 KiB, i.e.
       about 48 KiB at the audio rate. For a device that reads at exactly the audio rate that
       is about 1.1 s at 22050 Hz mono and about 3 s at 8 kHz mono, well inside 10 s, whatever
       the chunk size (PR #16 re-review 5569307179, nit 1).
     - A device that stops reading mid-stream fills the socket buffers. The next slice then
       blocks and is cut off after 10 s, so the GET ends incomplete. On that error path the
       connection is aborted, not closed: `close()` would first flush to the stalled peer.
     - Not bounded: the writer's wait for the next chunk, i.e. `PlaybackBegin` to
       `PlaybackEnd`. That bound is the producer's (P1 session/TTS carry-forward).
2. **Driver (`EsphomeSatellite`, optional `playback_server`; the wake-test runner has none):**
   - **`PlaybackBegin`:** assert disarmed (MP-AUDIO-001) and no playback active. Open the
     stream, then send `media_player_command(key, media_url=…, announcement=True)`. If a volume
     was given, it is sent first, with the old 0.1 s pause.
     - URL host: the hub's IPv4 address on the route to the device, found with a UDP connect
       as in the old driver. The function is injectable.
     - The device address is the API connection's `connected_address`, as IPv4 (an
       IPv4-mapped address is unwrapped); the server is IPv4-only. There is no DNS fallback:
       a missing `connected_address` is a `RuntimeError` at connect. A pure IPv6 connection
       gives a WARNING at connect and makes `PlaybackBegin` unavailable (a reconnect may use
       IPv4).
     - It first waits for a pending stop of a stray device stream, as `StartListening` does.
     - Media player: the first API-exposed `MediaPlayerInfo` found at connect. Missing gives a
       WARNING at connect and a loud `RuntimeError` at `PlaybackBegin`; capture is unaffected.
   - **`PlaybackChunk`:** queue the PCM. `StreamFull` makes the driver unavailable.
   - **`PlaybackEnd`:** end the stream and return at once. A drain task then does three steps:
     - The device must fetch the stream within 5 s, or the driver becomes unavailable.
     - It waits until the GET ends, at most the audio length plus 30 s.
     - If the device got all of it, it sleeps the remaining audio plus 0.2 s (Q2) and emits
       `PlaybackFinished`.

     An incomplete stream (the GET failed, or stalled and was cut off) makes the driver
     unavailable, with no `PlaybackFinished`, like a stream that was never fetched (PR #16
     review B1). The session recovers. Any other error in the drain is logged and re-raised
     from `wait_for_event()`, so it fails loudly in the session. A transport stop during the
     drain cancels it.
   - **`close()`** cancels the drain and releases the stream. `StartListening` and `PlayCue`
     assert that no playback is active.
3. **Cues (Q1):**
   - A cue service whose `supports_response` is OPTIONAL or ONLY is called with
     `return_response=True`; STATUS is called with `False` (success only). Both use an 8 s
     timeout (the session waits 10 s for `CueFinished`), and `CueFinished` follows the answer.
   - ESPHome answers only from an explicit `api.respond`. The P2 firmware uses status mode and
     ends each cue action with `api.respond: {success: true}` after `script.wait`, so the answer
     comes once the cue (about 1 s) has played: well inside the 8 s (PR #16 review B2).
   - An answer with `success=False` is a WARNING, and the cue still finishes. No answer gives
     `MicrophoneUnavailable`.
   - Without `supports_response` the call stays fire-and-forget. That is logged at INFO at
     connect.
4. **Tests (unit only; the fake media player GETs the announced URL over loopback):**
   - The server: header, chunks written before and after the GET, the 404 cases, malformed and
     silent requests, the stream limit, the remaining-time arithmetic, and close during a GET.
   - The driver:
     - the playback contract (`protocol.py`-checked), the URL, and the bytes the device got;
     - `PlaybackFinished` not before the audio length plus 0.2 s, and `PlaybackEnd` returning
       at once;
     - the volume order and the route address;
     - a device that never fetches, close during a drain, and the stream limit;
     - no media player, no server, and playback while armed.
   - Cues: OPTIONAL and STATUS wait for the answer; fire-and-forget without it; a failed answer
     and a missing answer.

## Questions for Maciej (decided above; kept for the record)

**Q1. A true `CueFinished` needs a firmware change.** The cue services already `script.wait`,
but without `supports_response` the API call returns at once. This repo has no firmware, and
the devices can't be reflashed now. Options:
- (a) Copy `firmware/esphome/` into the repo in T-014, change the two cue services, and reflash
  later.
- (b) Driver side only, as in step 3: it uses the response when a device offers one and falls
  back to fire-and-forget. The firmware change goes into the P2 firmware task, together with the
  copy and the reflash: status mode with `api.respond` after `script.wait`. That is corrected in
  review B2; `supports_response` alone is not enough.
- (c) Defer it all to P2.

*Recommendation: (b).* Small and safe now, and the firmware lives in one place (P2).

**Q2. Drain detection: stream timing or media player state?**
- (a) As the old repo: the stream has been sent, then the rest of the audio length plus 0.2 s.
  The old repo used this on the same hardware.
- (b) Subscribe to the media player state and finish on `ANNOUNCING` → `IDLE`. This reports
  what the device actually did, but the driver needs a state subscription, and how
  `speaker_source` reports announcements on Voice PE isn't verified.

*Recommendation: (a) for T-014.* Design §4.1 says "when the stream drains", and it can be
tested without hardware. Revisit (b) at the P1 hardware test if replies get cut off or the
next arm comes too early.

**Q3. Is `play` with a consumer URL (design §4.1 step 6) part of T-014?** It needs a new driver
command (for example `PlayUrl`) that `messages.py`/`protocol.py` don't have. The hub sees no
stream for it, so its end can only come from the media player state (Q2 (b)).

*Recommendation: no.* T-014 does PCM-stream playback, which is what `speak` needs. `play` goes
with the P1 commands, and its end detection is decided then.

## Out of scope

Session/API commands (`speak`, `play`, `stop`, `release`, the `speaking` state), Piper, firmware
edits (unless Q1 is (a)), and live hardware tests. Maciej is running his acceptance test on the
deployed hub, so no satellite connections from anything else.

## Done when

`make check` is green three times and CI is green. A PR with a manual hardware checklist for
later (the P1 hardware test): a WAV through `PlaybackBegin`/chunks/`End` plays on voice-pe-02,
and `PlaybackFinished` comes within about 0.5 s of the audible end.
