# T-014: ESPHome playback: plan

**Status:** waiting on Maciej's answers to Q1–Q3 below. Nothing is implemented yet.

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

## Proposed implementation (the recommended answers below)

1. **`hub/playback_http.py`: one server, asyncio only (`asyncio.start_server`, no new
   dependency).**
   - It binds `0.0.0.0:<playback_http.port>`, with host networking (§9), and is created and
     closed by main like the API.
   - `open(playback_id, rate, width, channels)` returns a stream with bounded buffering.
     `GET /<playback_id>.wav` sends the endless WAV header and then the chunks as they arrive.
   - An unknown id or a second GET returns 404. Every wait has a timeout. A stream nobody
     fetched within 5 s fails loudly.
2. **Driver (`EsphomeSatellite`):**
   - **`PlaybackBegin`:** assert disarmed (MP-AUDIO-001, the same as cues) and no playback
     active. Open the stream and send `media_player_command(key, media_url=…,
     announcement=True)`, plus `volume=` first if one was given.
     - The URL host is the local IP of the route to the satellite. Resolving it is a small
       injectable function, so tests don't touch the network.
     - The media player key is the single API-exposed `MediaPlayerInfo` found at connect.
       There is no media player check at connect, so capture keeps working without one;
       `PlaybackBegin` fails loudly instead.
   - **`PlaybackChunk`:** write to the stream (full buffer: fail loudly).
   - **`PlaybackEnd`:** end the stream, wait for the drain in a task, then emit
     `PlaybackFinished`. `send_output_event` does not block for the length of the audio.
   - **Failures:** a device that never fetches, a dropped connection or a timeout give
     `MicrophoneUnavailable` (the session recovers). `close()` cancels the playback and closes
     the stream.
3. **Cues (carry-forward, Q1):**
   - If a cue service reports `supports_response` other than NONE, call it with
     `return_response=True` and a timeout, and emit `CueFinished` when it answers.
   - Otherwise keep today's fire-and-forget behaviour and log it once at connect.
4. **Tests (unit only, no hardware):**
   - The fake client from `test_esphome_driver.py`.
   - A real asyncio HTTP GET against the server on port 0: header, chunks, end, 404 and
     timeouts.
   - The order `PlaybackBegin` → chunks → `PlaybackEnd` → `PlaybackFinished`, checked by
     `protocol.py`.
   - Drain timing with an injected clock and sleep.
   - Close during playback, a device that never fetches, and both cue paths.

## Questions for Maciej (each changes the implementation)

**Q1. A true `CueFinished` needs a firmware change.** The cue services already `script.wait`,
but without `supports_response` the API call returns at once. This repo has no firmware, and
the devices can't be reflashed now. Options:
- (a) Copy `firmware/esphome/` into the repo in T-014, change the two cue services, and reflash
  later.
- (b) Driver side only, as in step 3: it uses the response when a device offers one and falls
  back to fire-and-forget. The firmware change (`supports_response: optional` on the cue
  services) goes into the P2 firmware task, together with the copy and the reflash.
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
