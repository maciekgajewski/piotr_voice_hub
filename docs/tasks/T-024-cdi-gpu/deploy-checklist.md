# T-024 deploy checklist

Not part of this task. Run it on piotr only after Maciej says to deploy the merged commit.
Do not rebuild images. Do not `docker compose down`. Do not `docker image prune` or
`docker system prune`. Do not `docker compose config` or `docker compose convert`, and do not `cat` or
`docker exec` the hub config: printing it would show secrets. Do not pass
`docker-compose.stt-oneshot.yml`.

`docker exec` has no `-i` and no `-t`, and the command is followed by `</dev/null`, so it
cannot read the rest of a pasted script.

Record every expect line in this task's `log.md`.

## 0. CDI is on (Piotr Sysadmin if it is not)

Read-only. Do not edit `/etc/docker/daemon.json` and do not restart Docker in this deploy.

```
docker info 2>/dev/null | sed -n '/[Cc][Dd][Ii]/,/^$/p'
docker info 2>/dev/null | grep -F 'nvidia.com/gpu=all'
```

Expect a CDI spec directory list that includes `/var/run/cdi`, and a line that mentions
`nvidia.com/gpu=all`. design.md §3 already recorded both on 2026-10-18 (Docker 29.1.3).
If both are present, CDI is enabled. Do not add `"features": {"cdi": true}`.

If the spec directories are absent, stop. Enabling CDI is root work for Piotr Sysadmin
only, and only with Maciej's OK, because it means a Docker restart (every container):
add `"features": {"cdi": true}` to `/etc/docker/daemon.json` and restart `docker`. That is
not this checklist. The spec file itself is `/var/run/cdi/nvidia.yaml`, regenerated at
boot; do not generate it from this task.

## 1. Production is healthy, then pin the images

In `~/src/piotr_voice_hub`. `git status --porcelain` must be empty. Then:

```
export PROD="$(git rev-parse HEAD)"
export PROD7="$(git rev-parse --short=7 HEAD)"
echo "PROD=$PROD PROD7=$PROD7"
```

**PROD** is the commit to roll back to. **PROD7** is the rollback tag suffix (7 hex
digits, longer only if that prefix is ambiguous).

```
docker compose ps
curl -fsS http://127.0.0.1:8750/healthz; echo
ss -Htan '( sport = :8750 or sport = :8751 )'
docker logs piotr-voice-hub-hub-1 2>&1 | grep -E 'API .* (dis)?connected \(' | tail -1
docker inspect -f '{{.Name}} id={{.Id}} image={{.Image}} restarts={{.RestartCount}} started={{.State.StartedAt}}' \
  piotr-voice-hub-hub-1 piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1
docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' piotr-voice-hub-hub-1
```

Expect:

- `hub`, `piper`, and `speaker-recognition` are `healthy`.
- `/healthz` is JSON and both configured satellites have `"connected": true`. Record the
  body (it has names and states, not the API keys).
- `ss` prints only `LISTEN` lines. No `ESTAB` on 8750 or 8751. The API log line is absent
  or ends in `(0 clients)`. Otherwise stop and disconnect that client.
- Hub mounts are `/config/hub.yaml` and `/hf` only. If `/stt-oneshot` is mounted, stop:
  this deploy uses the standard compose and would drop that bind.
- Record the three container ids, image ids, restart counts and start times. Speaker's
  id must be the same at the end (it is not recreated).

Tag the running images, by id, not by assuming a rebuild. This deploy does not build, so
tag `hub` and `piper` (their containers are recreated). Do not tag speaker.

```
docker tag "$(docker inspect -f '{{.Image}}' piotr-voice-hub-hub-1)" "piotr-voice-hub:rollback-${PROD7}"
docker tag "$(docker inspect -f '{{.Image}}' piotr-voice-hub-piper-1)" "piotr-voice-hub-piper:rollback-${PROD7}"
docker image inspect -f '{{.Id}}' piotr-voice-hub:latest piotr-voice-hub-piper:latest
```

Expect each `:latest` id to equal the running container's image id. If it does not, stop:
`up` would not be the image that is running. Do not retag `:latest` by hand.

VRAM, after nothing else is using the GPU (design §4.3: hub about 2.2 GB, Piper about
2.2 GB):

```
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | while IFS=', ' read -r pid mem unit; do for c in hub piper; do docker top piotr-voice-hub-$c-1 -eo pid | grep -qw "$pid" && echo "$c $mem $unit"; done; done
```

Record the two lines as **VRAM1**.

Take the env from the running containers. This prints paths, not the config file:

```
export HUB_CONFIG="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/config/hub.yaml"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-hub-1)"
export HUB_USER="$(docker inspect -f '{{.Config.User}}' piotr-voice-hub-hub-1)"
export HUB_CACHE="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/hf"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-hub-1)"
export SPEAKER_PROFILES="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/profiles"}}{{.Source}}{{end}}{{end}}' piotr-voice-hub-speaker-recognition-1)"
test -n "$HUB_CONFIG" && test -n "$HUB_USER" && test -n "$HUB_CACHE" && test -n "$SPEAKER_PROFILES" && echo env-ok
```

Expect `env-ok`. Keep the exports in the shell that runs step 2.

## 2. Recreate hub and piper on the merged commit

`<merged sha>` is the merge commit Maciej named. It is not this checklist's job to pick one.

```
git fetch origin
git checkout --detach <merged sha>
git rev-parse HEAD
test -n "$HUB_CONFIG" && test -n "$HUB_USER" && test -n "$HUB_CACHE" && test -n "$SPEAKER_PROFILES"
docker compose up -d --wait --wait-timeout 300 --force-recreate --no-deps --no-build piper
docker compose up -d --wait --wait-timeout 300 --force-recreate --no-deps --no-build hub
```

Piper first. A new Piper container compiles CUDA kernels once (about 50 s, then healthy).
`--no-deps` does not wait for that, so hub is the second command. `--force-recreate` is
required: a restart keeps the old allowlist. `--no-build` leaves the images alone.
Speaker is not in either command.

If this compose rejects `--wait`, drop `--wait --wait-timeout 300` and poll
`docker compose ps` every 5 s, at most 5 minutes per service, until it says `healthy`.

Expect: new container ids for hub and piper (different from step 1); speaker's id
unchanged; both new containers `healthy`; `RestartCount` 0.

## 3. Devices and a new CUDA process (no root)

Expected `DeviceAllow` form, from `systemctl show docker-<64 hex id>.scope -p DeviceAllow`.
systemd prints one property, space-separated, sometimes wrapped:

```
DeviceAllow=char-195:* rwm char-511:* rwm
```

or one entry per minor (`char-195:0 rwm`, `char-195:1 rwm`, … and the same for `511`).
Some versions omit the `char-` prefix and print `195:*`. The check accepts `char-195`,
`char-195:*`, `char-195:<minor>`, `195:*`, and `195:<minor>`, and the same for `511`.
A minor of another device (`char-1:195`) does not count. An empty `DeviceAllow=` is a
failure. `LoadState` must be `loaded`.

`tools/check_gpu_cdi.sh` does that, plus `nvidia-smi`, plus a new-process CUDA init.
It does not use `-i` or `-t`.

```
tools/check_gpu_cdi.sh piotr-voice-hub-hub-1 piotr-voice-hub-piper-1
```

Expect exit 0.

- Hub prints `1` from `ctranslate2.get_cuda_device_count()` (the image has CTranslate2;
  its onnxruntime is the CPU build, so do not use that).
- Piper prints `cuda` from one `OrtValue` on device 0 (the image has onnxruntime-gpu
  1.26.0 and numpy, and no CTranslate2 or torch). The process then exits, so it does not
  hold VRAM.
- `nvidia-smi` in each container shows the RTX 5060 Ti.

The same commands by hand, if you are not using the script:

```
id=$(docker inspect -f '{{.Id}}' piotr-voice-hub-hub-1)
systemctl show "docker-${id}.scope" -p LoadState -p DeviceAllow --no-pager
docker exec piotr-voice-hub-hub-1 nvidia-smi </dev/null
docker exec piotr-voice-hub-hub-1 python -c 'import ctranslate2; n = ctranslate2.get_cuda_device_count(); print(n); raise SystemExit(0 if n > 0 else 1)' </dev/null
id=$(docker inspect -f '{{.Id}}' piotr-voice-hub-piper-1)
systemctl show "docker-${id}.scope" -p LoadState -p DeviceAllow --no-pager
docker exec piotr-voice-hub-piper-1 nvidia-smi </dev/null
docker exec piotr-voice-hub-piper-1 python -c 'import numpy as np, onnxruntime as ort; v = ort.OrtValue.ortvalue_from_numpy(np.zeros(1, np.float32), "cuda", 0); print(v.device_name()); raise SystemExit(0 if v.device_name() == "cuda" else 1)' </dev/null
```

Do not run these inside the service processes. `docker exec` starts a new process; that
is the case that fails after a reload today.

After both one-shot processes have exited, repeat the VRAM pipeline from step 1. Record
it as **VRAM3**. It should be the same two services and the same ballpark as **VRAM1**
(a few tens of MiB of context noise is normal; a missing piper or hub line is not).

## 4. daemon-reload (Piotr Sysadmin only)

```
sudo systemctl daemon-reload
```

Nothing else. No `systemctl restart docker`, no container restart. If you are not
Sysadmin, stop and ask. Step 5 is only meaningful after this returns 0.

## 5. The same check after the reload

Repeat step 3 (`tools/check_gpu_cdi.sh` or the hand commands). Expect the same: majors
195 and 511 still listed, `nvidia-smi` works, hub prints `1`, piper prints `cuda`.

Then:

```
curl -fsS http://127.0.0.1:8750/healthz; echo
ss -Htan '( sport = :8750 or sport = :8751 )'
docker inspect -f '{{.Name}} id={{.Id}} restarts={{.RestartCount}} started={{.State.StartedAt}}' \
  piotr-voice-hub-hub-1 piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1
```

Expect: the same two satellites `"connected": true`; `ss` still has no `ESTAB`; hub and
piper ids unchanged since step 2; `RestartCount` still 0; speaker id unchanged since
step 1. VRAM, sampled after the one-shot CUDA processes have exited, matches **VRAM3**
(same processes, same MiB apart from a few tens).

If any of that fails, do step 6.

## 6. Rollback

```
git checkout --detach "$PROD"
docker tag "piotr-voice-hub:rollback-${PROD7}" piotr-voice-hub:latest
docker tag "piotr-voice-hub-piper:rollback-${PROD7}" piotr-voice-hub-piper:latest
docker compose up -d --wait --wait-timeout 300 --force-recreate --no-deps --no-build piper
docker compose up -d --wait --wait-timeout 300 --force-recreate --no-deps --no-build hub
```

No `--build`. Then the step 3 hand commands (the helper script is not on **PROD**), plus
`/healthz`, `ss`, and restart counts as in step 5. The old allowlist is the pre-CDI one:
it lists 195 and 511 until the next daemon-reload, which is the bug this deploy is
fixing. Do not prune the `rollback-*` tags.
