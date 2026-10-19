# T-012: main entrypoint and docker compose

## Goal

`piotr-voice-hub --config PATH` runs the P0 hub (design.md §4.1, §6, §9, §11): STT, the
WebSocket API and one `SatelliteSession` per satellite, in a container on piotr. P0 exit
criterion: a client on the API prints `wake`/`transcript_final` from the satellites.

## Wiring (`src/piotr_voice_hub/hub/main.py`, called from `cli.main`)

- Load the config (exit 2 on `ConfigError`, as today), set up logging.
- Build: `FasterWhisperSpeechToText(config.stt)`, `ApiServer(api.host, api.port)`, one shared
  Silero session, and per satellite `SatelliteSession(s, EsphomeSatellite(s, SileroVad(...)),
  stt, emit=api.publish)`.
- Start in order: `stt.start()` (load and warm-up, before arming, §9), `api.start()`, then one
  task per `session.run()`. `/healthz` answers only once the model is warm, so "healthy" means
  ready.
- Run until SIGTERM/SIGINT (cancel the main task) or until a `run()` task ends: `run()` only
  returns by raising (invariant failure), since `MicrophoneUnavailable` is handled inside it.
- **Shutdown order** (README carry-forwards): cancel each `run()` task and wait for it, then
  `shutdown()` every session concurrently (bounded, best effort), then `api.close()` (bounded
  at 5 s), then the STT. The STT has no `close()` (T-008): the model is freed at process exit.
- **Exit codes:** 0 after a signal; 1 when a session fails (CRITICAL with traceback, after the
  clean shutdown above) or startup fails (e.g. the API port is taken), so Docker restarts it
  (§9); 2 for an invalid config; 70 stays the STT's own fatal exit (`exit_process`, T-008).
- **Log levels:** `aioesphomeapi` at ERROR (its own WARNING per failed connect duplicates the
  session's; PR #12 review), the API's websockets logger at WARNING (it logs every
  `/healthz` at INFO), `faster_whisper` at WARNING (one INFO line per model call, i.e. every
  partial).
- `piotr-voice-hub-healthcheck --config PATH`: GET `/healthz` on `api.port` (127.0.0.1 when
  `api.host` is a wildcard), exit 0 on 200, else 1. Used by the compose healthcheck, so it
  follows the config's port and host.

## Container

- `docker/hub.Dockerfile`: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04`, uv 0.13.1, Python
  3.12 via uv, `uv sync --locked --no-dev --no-editable` into `/opt/venv`. CPU `onnxruntime`
  only (§9). No Avahi: aioesphomeapi resolves `.local` names itself (zeroconf, host network;
  verified by the T-013 runs), so design.md §9 is updated.
- `docker-compose.yml` (project name `piotr-voice-hub`, one service `hub`; Piper and speaker
  recognition are P1): `gpus: all`, `network_mode: host`, `restart: unless-stopped`,
  `init: true`, `stop_grace_period: 20s`.
  - Config: `${HUB_CONFIG}` mounted read-only at `/config/hub.yaml`. It stays mode 600: the
    container runs as `${HUB_USER}` (`$(id -u):$(id -g)` of the config's owner), not root, so
    the permissions are never loosened.
  - Model cache: `${HUB_CACHE:-~/.cache/piotr_voice_hub/huggingface}` bind-mounted at `/hf`
    (`HF_HOME`), the same cache the T-013 runner fills, so no second download.
  - Healthcheck: `piotr-voice-hub-healthcheck` (liveness, §6), start period 180 s with 2 s
    start interval, then every 30 s.
- VRAM: one `large-v3-turbo` float16 model (about 2.1–2.3 GB, §4.3); nothing else on the GPU.
- Ports: 8750 (API) is free on piotr; no existing container uses it (old ai-server used 2137
  and is stopped). 8751 (playback) is not opened in P0.

## PR #13 re-review nits

- n1: a failed WebSocket handshake (port scan, garbage, EOF) logs one WARNING line without a
  traceback: a filter on the API's websockets logger rewrites websockets' "opening handshake
  failed" ERROR + traceback.
- n2: the "refusing … clients connected" WARNING at most once per 60 s, with the count of
  refusals suppressed since the last one.
- n3: a comment on what stays pending after `close()`'s final wait.

## Tests

- Fakes for STT, API and sessions: start order; shutdown order (cancel + wait every `run()`,
  then every `shutdown()`, then `api.close()`); a failing session → exit 1 after a full clean
  shutdown; a signal → exit 0; a startup failure → exit 1; log levels; healthcheck.
- API: n1 (EOF, garbage, plain HTTP without upgrade: one WARNING each, no traceback) and n2.

## Live check on piotr (no speech)

Image builds; container healthy; `/healthz` shows voice-pe-02/03 connected and `idle`; a
one-line WS client gets the snapshot; VRAM and time to healthy recorded; `docker compose down`
of our project only. No other container is touched.
