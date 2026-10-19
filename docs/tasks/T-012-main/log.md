# T-012 log (append-only)

## 2026-10-19: start

- Branch `t-012-main` from origin/main e71436d (PR #13, T-011, squash-merged).
- Read design.md §4.1, §6, §9, §11, the README carry-forwards (both T-012 items: shutdown
  order and `aioesphomeapi` at ERROR; API wiring and the websockets logger), the old
  `docker/ai-server.Dockerfile`, `docker-compose.ai-server.yml` and its entrypoint (read-only).
  Wrote plan.md. No design question changes the implementation.
- The old image ran avahi-daemon and dbus for `.local` names. Not needed here: aioesphomeapi
  resolves them itself (the T-013 container ran without them). design.md §9 is updated.
- Ports on piotr: 8750 and 8751 are free. Existing containers listen on 11434 (Ollama,
  loopback), 2140 (speaker recognition), 10200 (Piper) and 2283/5433 (Immich, loopback). The
  old ai-server's WebSocket was 2137, and it is not running.

## 2026-10-19: implementation

- `hub/main.py`: `serve` (start order, shutdown order, exit 1 on a session failure),
  `run_until_signal` (SIGTERM/SIGINT → exit 0), `run_hub` (real wiring),
  `quiet_library_loggers`. `cli.main` runs it; unexpected errors → CRITICAL, exit 1.
  `piotr-voice-hub-healthcheck` for the compose healthcheck.
- `faster_whisper` is also raised to WARNING: it logs one INFO line per model call (every
  partial), as the T-013 runs showed.
- API nits n1–n3: one-line WARNING for failed handshakes (a filter on the API's websockets
  logger, which is now our own logger), refusals logged once per 60 s with a count, and a
  comment on what stays pending after `close()`'s last wait.
- `docker/hub.Dockerfile`, `.dockerignore`, `docker-compose.yml` (project `piotr-voice-hub`).

## 2026-10-19: live run on piotr (no speech)

- `docker compose build`: 21 s (base image cached), image 8.21 GB. Only `onnxruntime` is
  installed (no `onnxruntime-gpu`).
- `HUB_CONFIG=~/hub-wake.yaml HUB_USER=1002:1002 docker compose up -d`. The config stayed mode
  600; the container runs as its owner, so it reads it.
- Healthy 11.5 s after `up` (model load and warm-up 10.0 s). `/healthz`: voice-pe-02 and
  voice-pe-03 connected and `idle`; voice-pe-bedroom and box3-office `error` (not on mDNS;
  their sessions back off with one WARNING per attempt, as designed).
- VRAM: 2220 MiB for the hub process (2238 MiB in use on the GPU; 12 MiB before).
- `uv run --locked python -m websockets ws://localhost:8750/ws` printed the four `satellite`
  snapshot events. No speech, so no other events.
- Restart check: `kill -KILL` of the hub process (the child of docker-init). Docker restarted
  the container (RestartCount 1), and both satellites were listening again about 5 s after
  the kill (model 2.2 s from the page cache). Docker's health status stays `healthy` across a
  restart unless 3 checks fail, so the check uses `/healthz` and the logs.
- `docker compose stop`: 0.6 s, exit 0. Both satellites got `StopListening(reason=shutdown)`,
  then "hub stopped", "stopped by signal". `docker compose down` removed only our container.
  VRAM back to 12 MiB. No other container was touched. Ollama and immich-server had been
  restarted at 07:34 UTC, before this run started; that was not us.
- `~/hub-wake.yaml`: the box3-office address was updated to `piotr-box3-01-cbfaA8.local`, in
  place, keeping mode 600.
