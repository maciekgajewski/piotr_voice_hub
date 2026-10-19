# T-016: Wyoming Piper TTS client: plan

**Status:** implemented as below (2026-10-19). Based on `t-015-piper-service` (PR #18), which
it needs for the compose service and `tts.uri` on 10201; the PR's base is `main`, and it is
rebased after #18 merges. No design question changes the implementation (see the end).

## Scope (docs/tasks/README.md)

> **T-016 tts-client** (depends on T-004, T-015): `tts/`: Wyoming Piper client (old
> `PiperTextToSpeech.synthesize`), streamed PCM chunks, a timeout on every wait, a text-length
> cap per call; warm-up at hub startup before arming.
>
> Decided: the Piper service warms itself before it listens (T-015), so the hub's startup step
> is only a reachability check: a Wyoming `describe` → `info` round trip, bounded, before any
> satellite is armed. A failure exits 1.

From Maciej's T-016 brief (2026-10-19): the client streams the synthesized audio out for T-017
to feed to T-014's playback (the format, then an async iterator of PCM chunks); bounded
timeouts at connect, first audio and between chunks; a text cap (follow design.md; if it says
nothing, reject over the cap with a clear error, documented); a fail-loud error type; no
retries unless design.md says so (it does not).

Out of scope: the session using it, `speak`/`release`, volume and playback (T-017); the
speaker hint (T-018).

## What exists

- **Old project** (read only), `ai_server/microphones/tts.py`, `PiperTextToSpeech`:
  - `start()`: waits up to 30 s for the TCP port, then a validation synthesis of
    "Dzień dobry." (the service warms itself now, so this becomes a `describe`).
  - `synthesize(text)`: `wyoming.client.AsyncTcpClient`, one `Synthesize(text,
    voice=SynthesizeVoice(name=voice))`, then yields start / chunk / end events until
    `audio-stop`; EOF before it is a `RuntimeError`; the connection is closed in `finally`.
    No timeouts on any read, no text limit, and other event types (including `error`) are
    silently skipped. design.md §5: copy `synthesize` only, drop `_stream_to_box`.
- **design.md:** §4.1 step 6 (Piper over Wyoming, then the shared playback stream), §5 (`tts/`),
  §7 (`tts: {uri, voice, volume}`), §9 (timeouts on every await, fail loudly). It says nothing
  about text length or retries.
- **Our Piper service (T-015):** non-streaming `synthesize` is split into sentences by
  wyoming-piper; it sends `audio-start` once, then `audio-chunk`s (1024 samples, 22050 Hz,
  16-bit, mono) per sentence, then `audio-stop`; a failure is an `error` event and the
  connection closes. Warm: 0.05–0.18 s per request (T-015 log).

## Implementation

1. **Dependency:** `wyoming==1.10.2` (pure Python, no dependencies, typed; the same version as
   the service). Only its event framing and event classes are used (`async_read_event`,
   `async_write_event`, `Synthesize`, `AudioStart/Chunk/Stop`, `Error`, `Describe`, `Info`);
   the connection is our own `asyncio.open_connection`, so every wait has a timeout.
2. **`tts/interfaces.py`:** `AudioFormat(rate, width, channels)`; `TtsError` (fail loud: the
   service is unreachable, slow, closed early, sent an `error` or anything unexpected);
   `TtsTextError(TtsError)` (the text itself is rejected: empty, or over the cap; T-017 maps it
   to `bad_request`); the `TextToSpeech` and `Speech` protocols.
3. **`tts/wyoming_piper.py`, `WyomingPiperTts(config: TtsConfig)`:**
   - `start()`: the startup reachability check: connect, `describe`, read `info` within the
     bounds; it fails unless `info` lists a TTS program with the configured voice installed.
     (Found live: wyoming-piper lists its whole catalogue as installed, so this cannot catch a
     `tts.voice` other than the service's pinned one; that is a `TtsError` at the first
     synthesis, from the service's `error` event. See the questions.)
   - `synthesize(text)` is an async context manager:
     ```python
     async with tts.synthesize(text) as speech:
         speech.format  # AudioFormat, from audio-start
         async for pcm in speech:  # bytes, until audio-stop
             ...
     ```
     It validates the text first (no connection for a rejected text), connects, sends one
     `synthesize` with the configured voice, and waits for `audio-start`. The iterator yields
     each chunk's PCM and ends at `audio-stop`. Leaving the block closes the connection,
     also when the consumer stops early or fails.
   - **Bounds** (constants, like the other modules' protocol bounds):
     - connect: 5 s;
     - `info` after `describe`: 5 s;
     - first audio (`audio-start` after the request): 10 s (warm service: 0.05–0.08 s; the
       service serializes requests, so a concurrent one waits a sentence or two);
     - between events while streaming: 10 s (a sentence's synthesis is the longest gap,
       0.14 s for 194 characters).
     A bound that fires is a `TtsError` naming the wait.
   - **Text cap: reject, do not split** (design.md is silent; the brief's fallback). Text is
     stripped; empty text or more than 1000 characters is a `TtsTextError` with the length
     and the limit. 1000 characters is about 65 s of audio (T-015: 191 characters gave
     12.4 s), well under the playback's 300 s per stream, and about 1 s of synthesis. The
     service still splits it into sentences, so no single inference is long. (The README note
     had "split into calls"; it now says reject, as decided in the brief.)
   - **Strict events:** an `error` event is a `TtsError` with its text and code; EOF before
     `audio-stop`, chunks before `audio-start`, a chunk in another format than the start, or
     any other event type is a `TtsError`.
   - **No retries.** One log line per synthesis: characters, audio seconds, first-audio and
     total time.
4. **Config:** `tts.uri` must be `tcp://host:port` (a `ConfigError`, exit 2, otherwise).
5. **Hub startup:** `run_hub` awaits `tts.start()` before anything else (before the playback
   server, the STT warm-up and the sessions), so a missing Piper exits 1 before any satellite
   is armed. The client is not used further until T-017.
6. **Compose:** the `hub` service `depends_on: piper: condition: service_healthy`, so a
   deploy starts Piper first and the hub's check does not crash-loop during its 50 s cold
   start.

## Tests

- Unit (`tests/test_tts.py`, a fake Wyoming server on loopback): describe ok, voice missing,
  no TTS program, no answer (timeout), closed; synthesize: format and chunks in order, the
  request carries the voice and the stripped text, `error` event, EOF mid-stream, no
  `audio-start` (timeout), a stall between chunks (timeout), chunks before start, a format
  change, an unknown event, connect refused and connect timeout, rejected text (empty, over
  the cap, exactly at the cap) without a connection, early exit closes the connection; config
  `tts.uri` validation.
- GPU-gated (`tests/test_tts_gpu.py`, marker `gpu`, with `PIOTR_VOICE_HUB_PIPER_URI`): against
  our T-015 service: `start()`, a short and a ~200-character sentence twice each (format,
  duration, latency printed), a 1000-character text accepted and streamed, 1001 rejected.
  `tools/piper-gpu-test.sh` runs it with T-015's test.
- Live: our `piper` service alone in its own compose project (`-p piotr-voice-hub-t016`) on
  10201, then stopped.

## Acceptance

- `make check` green 3×, CI green; the GPU test passes on piotr; measurements in log.md.

## Questions (none changes the implementation)

- `describe` cannot prove the voice is served (above). To catch a wrong `tts.voice` at
  startup, either T-015's service lists only its preloaded voice in `info` (a small change to
  PR #18 or a follow-up), or the startup check also synthesizes a word (about 0.08 s, but the
  brief decided describe-only). Left as is; the first synthesis fails loudly.
- The hub now needs Piper at startup (exit 1 without it), and compose starts the hub only once
  `piper` is healthy (about 50 s for a new container).

- The 1000-character cap is a constant, not a config key (design §7 has none). Move it to
  `tts` config only if a consumer needs longer replies.
