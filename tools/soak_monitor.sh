#!/usr/bin/env bash
# T-030 soak monitors: read-only, for docs/tasks/T-030-soak-72h/plan.md. Run each mode in its
# own tmux window under `timeout 73h`:
#
#   tools/soak_monitor.sh log    DIR START   # follow the hub log from START into DIR/hub.log
#   tools/soak_monitor.sh sample DIR         # /healthz every 30 s, resources every 5 min
#
# Never sends an API command, never execs into or restarts a container. The sampler writes
# DIR/ABORT and kills the tmux session (SOAK_TMUX, default t030), so every monitor stops,
# if DIR grows past 200 MB or / is over 90 % full.
set -euo pipefail

HUB=${HUB:-piotr-voice-hub-hub-1}
CONTAINERS=${CONTAINERS:-"$HUB piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1"}
HEALTH=${HEALTH:-http://localhost:8750/healthz}
MAX_DIR_MB=200
MAX_DISK_PCT=90

mode=${1:?usage: soak_monitor.sh log DIR START | sample DIR}
dir=${2:?missing DIR}
mkdir -p "$dir"
chmod 700 "$dir"

case $mode in
log)
  start=${3:?missing START (docker logs --since value, e.g. 2026-11-23T17:00:00Z)}
  # Re-run from START after a container restart: the file is rewritten whole, so it neither
  # duplicates nor loses lines while the json-file rotation still holds them.
  while true; do
    docker logs -f --since "$start" "$HUB" >"$dir/hub.log" 2>&1 || true
    sleep 10
  done
  ;;
sample)
  tick=0
  while true; do
    now=$(date -u +%FT%TZ)
    printf '{"ts":"%s","healthz":%s}\n' "$now" \
      "$(curl -s -m5 "$HEALTH" || echo null)" >>"$dir/health.jsonl"
    if ((tick % 10 == 0)); then
      for name in $CONTAINERS; do
        printf '%s,inspect,%s,%s\n' "$now" "$name" "$(docker inspect -f \
          '{{.State.StartedAt}},{{.RestartCount}},{{.State.Health.Status}}' "$name" \
          2>&1)" >>"$dir/res.csv"
        printf '%s,stats,%s,%s\n' "$now" "$name" "$(docker stats --no-stream --format \
          '{{.MemUsage}},{{.CPUPerc}}' "$name" 2>&1)" >>"$dir/res.csv"
      done
      nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits \
        2>&1 | while IFS=', ' read -r pid mib; do
        container=$(grep -o -m1 -E '[0-9a-f]{64}' "/proc/$pid/cgroup" 2>/dev/null | cut -c1-12)
        printf '%s,gpu,%s,%s,%s\n' "$now" "$pid" "${container:-host}" "$mib" >>"$dir/res.csv"
      done
      dir_mb=$(du -sm "$dir" | cut -f1)
      disk_pct=$(df --output=pcent / | tail -1 | tr -dc 0-9)
      printf '%s,disk,%s MB,%s%%\n' "$now" "$dir_mb" "$disk_pct" >>"$dir/res.csv"
      if ((dir_mb > MAX_DIR_MB || disk_pct > MAX_DISK_PCT)); then
        echo "$now ABORT disk: dir ${dir_mb} MB, / ${disk_pct} %" | tee "$dir/ABORT"
        tmux kill-session -t "${SOAK_TMUX:-t030}" 2>/dev/null || true # all monitors, not the hub
        exit 3
      fi
    fi
    tick=$((tick + 1))
    sleep 30
  done
  ;;
*)
  echo "unknown mode $mode" >&2
  exit 2
  ;;
esac
