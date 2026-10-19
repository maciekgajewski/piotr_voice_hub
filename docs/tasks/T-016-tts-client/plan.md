# T-016: Wyoming Piper TTS client: plan

**Status:** implemented as below (2026-10-19). First based on `t-015-piper-service` (PR #18);
rebased onto `main` after #18 merged (e8a5679). No design question changes the
implementation (see the end). The PR also carries the #18 review nits N1–N4 (a separate
commit, see the end) and a wake-test log entry, in the T-010 log (a separate commit). The
follow-up PR (review 5570976280) is at the end.

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
     bounds; it fails unless `info` lists exactly the configured voice, installed. Our service
     describes only the voice it serves (#18's `advertise_only`; stock wyoming-piper lists its
     whole catalogue as installed, found live before #18's final version), so a wrong
     `tts.voice` fails here, before anything is armed, with both voices in the message.
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
5. **Hub startup:** `run_hub` runs `check_tts` (`tts.start()`) before anything else (before
   the playback server, the STT warm-up and the sessions). A `TtsError` (Piper missing, slow,
   or describing another voice) logs one CRITICAL line, "TTS startup check failed, exiting:
   tts.voice is …, but Piper at … describes [...]", and exits 1 before any satellite is armed. The client is not used further until T-017.
6. **Compose:** the `hub` service `depends_on: piper: condition: service_healthy`, so a
   deploy starts Piper first and the hub's check does not crash-loop during its 50 s cold
   start.

## Tests

- Unit (`tests/test_tts.py`, a fake Wyoming server on loopback): describe ok; not exactly the
  configured voice (another voice, two voices, listed twice, not installed, none); no TTS
  program, no answer (timeout), closed; synthesize: format and chunks in order, the
  request carries the voice and the stripped text, `error` event, EOF mid-stream, no
  `audio-start` (timeout), a stall between chunks (timeout), chunks before start, a format
  change, an unknown event, connect refused and connect timeout, rejected text (empty, over
  the cap, exactly at the cap) without a connection, early exit closes the connection; config
  `tts.uri` validation. `tests/test_main.py`: `check_tts` passes, a `TtsError` is one
  CRITICAL line and exit 1, other errors propagate.
- GPU-gated (`tests/test_tts_gpu.py`, marker `gpu`, with `PIOTR_VOICE_HUB_PIPER_URI`): against
  our T-015 service: `start()`, a short and a ~200-character sentence twice each (format,
  duration, latency printed), a 1000-character text accepted and streamed, 1001 rejected,
  `start()` with another voice fails.
  `tools/piper-gpu-test.sh` runs it with T-015's test.
- Live: our `piper` service alone in its own compose project (`-p piotr-voice-hub-t016`) on
  10201, then stopped.

## Acceptance

- `make check` green 3×, CI green; the GPU test passes on piotr; measurements in log.md.

## Questions (none changes the implementation)

- (Resolved.) A wrong `tts.voice` could not be caught by `describe` while the service listed
  its whole catalogue; #18's final version describes only the served voice, and the check now
  requires exactly that voice.
- The hub now needs Piper at startup (exit 1 without it), and compose starts the hub only once
  `piper` is healthy (about 50 s for a new container).
- The 1000-character cap is a constant, not a config key (design §7 has none). Move it to
  `tts` config only if a consumer needs longer replies.

## #18 review nits (review 5570089492), in a separate commit

- **N1:** a third warm-up text, one sentence of 950 characters (near the 1000 cap), so a long
  call's first-use CUDA work happens before the port opens, not while serving under the 30 s
  watchdog. Its time is in the warm-up log line. The warm check still re-times the ~200
  character sentence (`WARM_CHECK_TEXT`).
- **N2:** the per-synthesis watchdog is `faulthandler.dump_traceback_later(timeout,
  exit=True)` instead of a `threading.Timer` (reasons in log.md).
- **N3:** `WARM_LIMIT_SECONDS` 1 s → 2 s.
- **N4:** the warm check's thread name and log lines say "warm check", not "warm-up"; the
  compose comment's long line is reflowed; the `MAX_TEXT_CHARS` comment says the client
  rejects (not splits) longer text.

## Follow-up (PR #19 review 5570976280), branch `t-016-followup`

- **S1:** malformed framing (`KeyError`, `TypeError`, `AttributeError` from wyoming's reader,
  which catches only `ValueError`) is a `TtsError`.
- **S2:** `TtsUnavailableError(TtsError)` for a refused, failed or timed-out connect and a
  reset connection. `hub/main.py` `check_tts` retries only that, every 2 s, starting no
  attempt after 120 s (about 137 s at most with the last attempt's 17 s bound; PR #20
  review N1) (`TTS_STARTUP_RETRY_SECONDS`, `TTS_STARTUP_TIMEOUT_SECONDS`, injectable with the
  clock and sleep), with a WARNING per attempt; a wrong voice, any other `TtsError` and the overall
  timeout stay one CRITICAL line and exit 1. Why: compose's `depends_on` orders only
  `docker compose up`; after a reboot or a daemon restart Docker starts both containers at
  once and Piper needs about 50 s. This is a startup wait, not a synthesis retry; synthesis
  still never retries.
- **S3:** DEBUG lines per interaction: describe (connect, info), synthesis (connect with the
  length, request sent, audio-start with format and latency, audio-stop, stopped early,
  cancelled, failed), each with a per-client synthesis id. Never the text itself.
- **N1 (decided: no client limit):** there is no overall deadline per synthesis in the client.
  Each wait is bounded (first audio 10 s, each next event 10 s), and T-017's bound from
  `PlaybackBegin` to `PlaybackEnd` in the session is the end-to-end bound per synthesis.
- **N2/N3:** tests: a cancel mid-stream, a connection reset mid-stream, and the real
  faulthandler watchdog disarmed after a successful synthesis (no exit after the timeout).
- **N4:** the wake-test wording above (T-010, not T-013).
- **N5:** `parse_tcp_uri` rejects a query, a fragment and user info (the latter without
  echoing the URI).
- **N6:** `tools/piper-gpu-test.sh`'s comment wrapped at 100.
