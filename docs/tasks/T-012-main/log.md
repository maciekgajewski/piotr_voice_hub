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

## 2026-10-19: PR #14 review 5568516304

- **B1:** compose `logging: json-file`, `max-size: 10m`, `max-file: "5"`; verified live with
  `docker inspect -f '{{.HostConfig.LogConfig}}'`: `{json-file map[max-file:5 max-size:10m]}`.
- **B2 (decision a):** `hub/watchdog.py`. A heartbeat task stamps the time every 1 s; a daemon
  thread checks every 1 s and, if the stamp is older than 30 s, logs CRITICAL and
  `os._exit(1)`. The intervals and the stall action are injectable. Tests: a missing
  heartbeat fires once with the CRITICAL line; a running loop stays quiet for five times the
  limit and `stop()` ends the thread; a subprocess whose loop is blocked by `time.sleep`
  exits 1 with the CRITICAL line (0.3 s limit); the hub's `run_until_signal` runs it and stops
  it after cleanup. The heartbeat keeps running through the bounded cleanup and the watchdog
  is stopped after it, so it can only fire if cleanup hangs the loop for 30 s (exit 1:
  acceptable, documented in plan.md). The compose healthcheck comment now says Docker only
  marks the container unhealthy; restarts come from the process exiting.
- **B3:** checklist Stop step: `docker compose stop`, `docker compose logs --tail 20`,
  `docker inspect -f '{{.State.ExitCode}}'` (0), `docker compose down`.
- **n1:** wording now matches the code: a session failure is logged CRITICAL at once, then
  the clean shutdown runs, then exit 1 (plan.md, PR body).
- **n2:** `serve` calls `before_cleanup` first in its `finally`; `run_until_signal` passes the
  handler removal (test: SIGTERM is back to `SIG_DFL` inside a session's `shutdown()`).
- **n3:** `asyncio.wait(tasks, timeout=10 s)` in the cleanup; a `run()` still running is logged
  and not shut down (its `shutdown()` would assert). Test with a session that keeps running
  after being cancelled. `stop_grace_period` raised to 30 s (10 + 3 + 5 s of bounds).
- **n4:** both bind mounts use `create_host_path: false`: a missing cache dir fails
  `compose up` at once ("bind source path does not exist", checked on piotr), instead of
  Docker creating a root-owned dir. The checklist does `mkdir -p` first.
- **n5:** `USER 65534:65534` in the Dockerfile (compose's `user:` still sets HUB_USER). Base
  images pinned by index digest: `nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d1…`
  and `ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f36…` (from `docker buildx imagetools inspect`;
  the CUDA one matches the local RepoDigest).
- **n6:** design.md §9: the API binds 0.0.0.0 with no auth; the LAN/Tailscale perimeter is
  the boundary.
- **n7, n8:** checklist notes the exit-2 restart loop on an invalid config (check the logs),
  and has `nvidia-smi` and `docker compose ps` steps.
- Live config: voice-pe-bedroom and box3-office are commented out in `~/hub-wake.yaml` (still
  mode 600); the full previous file is kept as `~/hub-wake.full.yaml` (mode 600).

## 2026-10-19: live re-run on piotr (no speech)

- Build with the pinned digests: OK. The image user is 65534:65534; the container runs as
  1002:1002 (HUB_USER).
- Healthy 11.5 s after `up`. `docker compose ps`: `Up 11 seconds (healthy)`. `/healthz`:
  voice-pe-02 and voice-pe-03 connected and `idle`, nothing else. VRAM 2220 MiB for the hub
  (2238 MiB in use, 12 MiB before and after).
- Log config verified (above).
- The hub ran about 6 minutes (08:04–08:10 UTC) with no restart and no CRITICAL, stalled or
  Traceback lines: the watchdog stayed quiet. Ambient sound gave 11 segments, none with a wake
  candidate, so there were no events.
- The SSH connection to piotr stalled once during this run ("timed out during banner
  exchange"). The control connection was restarted; the hub itself was unaffected.
- Stop sequence: `docker compose stop` 0.3 s; the last 20 log lines end with `stopped
  listening … reason=shutdown` for both satellites, `API closed`, `hub stopped`, `stopped by
  signal`; `docker inspect -f '{{.State.ExitCode}}'` gave `0`; `docker compose down` removed
  only our container. No other container was touched.

## 2026-10-19: merged; deployed; follow-up (re-review 5568670385)

- PR #14 was squash-merged as 48b5f70. The main checkout on piotr is fast-forwarded to it, and
  the hub is deployed from there for Maciej's acceptance test, exactly as in the PR #14
  checklist (`HUB_CONFIG=~/hub-wake.yaml`, `HUB_USER` = grok-developer,
  `docker compose up -d --build`). It was healthy after about 12 s; `/healthz` showed
  voice-pe-02 and voice-pe-03 connected and idle. It is left running. This follow-up is
  worked in a separate worktree, so the deployed checkout stays on 48b5f70.
- Event watch for a user without the repo or uv: system curl 8.18 supports `ws://`, but it
  writes the frames with no separator. jq reads by line, so it can't split them. The command
  pipes curl through a stdlib python3 splitter that prints one event per line (see the
  follow-up PR body). Checked on piotr with a clean env (`PATH=/usr/bin:/bin`) in a temporary
  HOME/cwd: it printed the two snapshot events at once and stayed connected for over 75 s
  (curl answers the pings).
- n1: before `os._exit(1)`, the watchdog's exit path dumps every thread's stack to stderr with
  `faulthandler.dump_traceback(all_threads=True)`. The subprocess test asserts the dump follows
  the CRITICAL line and shows the blocked main thread in `main`. The test fails with the dump
  removed.
- n2: the `RUN_CANCEL_TIMEOUT_SECONDS` comment is finished.
- `docs/tasks/README.md`: T-012 is merged; P0 is done except T-014 (playback).
