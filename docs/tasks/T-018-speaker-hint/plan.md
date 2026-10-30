# T-018: speaker hint: plan

**Status:** done (merged as PR #27, 2026-10-30).

## Goal

`transcript_final` carries `speaker`: `{name, confidence}` for the top ECAPA match above the
threshold, otherwise `null` (design.md §4.1 step 5, §6). The speaker is only a **hint**: no
failure of speaker recognition may delay an utterance by more than `timeout_seconds` or stop it.

## Scope (docs/tasks/README.md)

> **T-018 speaker-hint:** speaker-recognition service in the compose file (port 2141) and the
> HTTP client (`speaker/`); `transcript_final` carries `speaker`.
> **Ports note:** listen on 2141; T-018 updates design.md §7, §9 and
> `config/hub.example.yaml` from 2140.

## What exists

- Old project (read only, `ai_server/speaker_recognition/` @ a299643): an aiohttp service on
  2140 (`/health`, `/recognize`, `/recognize-stream`, `/reload`), SpeechBrain
  `spkrec-ecapa-voxceleb` on CPU (`--device cpu`), threshold 0.45. `/recognize-stream` takes
  16 kHz 16-bit mono PCM with `X-Audio-*` headers and `X-Voice-Profiles` (JSON name → `.npz`
  path), embeds it, and returns the cosine top match (`recognized_user` only at or above the
  threshold) and a confidence mapped from the score (0.5 at the threshold). The old session
  streamed the segment into it during capture and waited at most 1 s for the result.
- Hub: `speaker_recognition: {url, timeout_seconds, profiles}` is already in the config;
  `TranscriptFinal.speaker` and its API serialization exist and are always `None`.

## Design choices

1. **Service** (`src/piotr_voice_hub/speaker/service.py`, run by `docker/speaker/entrypoint.py`
   in `docker/speaker.Dockerfile`, compose service `speaker-recognition`):
   - The old `/recognize-stream` protocol and result, unchanged, so the hub client works with
     either service; `/health` for the healthcheck. `/recognize` and `/reload` are dropped (the
     hub sends its profiles with every request; nothing else uses them).
   - The old scoring copied (`profiles.py`: normalize, cosine, threshold, confidence mapping).
   - Stdlib `http.server` (one request at a time; the hub is the only client) and numpy, so the
     hub's tests cover all of it; torch and SpeechBrain only in the image's entry point
     (like Piper's, T-015). Bounded: a body cap (60 s of audio), a socket timeout per request,
     `Content-Length` required.
   - Profile paths must lie under `--profiles-dir` (the read-only mount); otherwise `400`.
   - CPU only (design §4.3: 0 GB VRAM), CPU-only torch wheels, no GPU requested. The model is
     baked into the image, pinned by Hugging Face revision and SHA-256 (like the Piper voice),
     never downloaded at runtime (`HF_HUB_OFFLINE=1`). A warm-up embedding before it listens.
   - **Loopback only:** `127.0.0.1:2141`, like Piper (the hub is the only client).
   - Runs as `HUB_USER`; `SPEAKER_PROFILES` (a host directory readable by `HUB_USER`) is
     mounted read-only at `/profiles`.
2. **Client** (`src/piotr_voice_hub/speaker/client.py`): one plain HTTP/1.1 POST per accepted
   utterance on `asyncio.open_connection` (no new dependency), the whole request bounded by
   `timeout_seconds`, the response size capped. It never raises for a service failure: a
   refused connect, a timeout, an HTTP error or a malformed answer is a WARNING and `None`.
   No profiles configured: no request, `None`.
3. **Session:** keeps the accepted utterance's audio (the wake word's segment too, after the
   wake word alone), at most the last `max_segment_seconds` (+0.1 s), and starts the request
   when it accepts, so it runs during the stop and the accepted cue; it waits for it (bounded
   by the client) after the `PROCESSING` visual and before `transcript_final`. A request still
   open when the session fails is cancelled.
4. **Hub startup:** no startup check and no compose `depends_on`: the hub runs without the
   service (every hint `null`, with a WARNING per utterance).
5. **Config:** `speaker_recognition.url` must be `http://host:port` (exit 2 otherwise);
   `timeout_seconds` stays 1.0 in the example. Port 2141 in design.md §4, §7, §9 and the
   example config.

## Out of scope

- Profile enrolment (`profile_builder.py`, `tools/speaker-profile-build.sh`): the profiles the
  old tool built are used as they are (same `.npz` format). Copy the tool when a new profile
  is needed.
- A threshold in the hub config (it stays the service's `--threshold`, 0.45 as before).
- The follow-up flag (T-020); speaker for follow-up answers comes with it for free.

## Steps

1. plan.md, log.md.
2. Config URL check; `speaker/` interfaces, client, service; entry point, Dockerfile, pinned
   requirements; compose service; `.dockerignore`.
3. Session and main wiring.
4. Tests: service (fake embedder, real HTTP on loopback), client (fake HTTP server: ok,
   below threshold, 500, garbage, slow, refused, oversize, no profiles), session (hint on
   `transcript_final`, audio sent, wake-word-alone audio, `None` on failure, cancel on
   disconnect), config.
5. Build the image as `piotr-voice-hub-speaker:t018` and run it on a scratch loopback port
   with a profile built from a test WAV; measure latency and RAM; tear down.
6. design.md, README status; `make check` green; push.

## Acceptance criteria

- `make check` green.
- The image builds, warms up, serves `/health`, and recognizes a test voice against its own
  profile (and `null` against an unrelated one) on loopback; latency well inside 1 s.
- On hardware (Maciej, PR checklist): an utterance by an enrolled speaker carries
  `speaker: {name, confidence}`; someone else, `null`; with the service stopped, the hub still
  accepts the utterance with `speaker: null` and one WARNING.
