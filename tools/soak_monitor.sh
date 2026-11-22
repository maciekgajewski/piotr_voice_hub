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
#
# No `set -e`: one failed sample (a host GPU process, a transient docker, curl, du or df
# error) must never end the sampler and its disk guard. Every step is checked by hand; a
# missing guard value is logged on every iteration (`missing`) and never skips the guard.
# Test knobs: SOAK_INTERVAL (s, default 30), SOAK_RES_EVERY (samples, default 10),
# SOAK_ITERATIONS (default 0 = until killed).
set -uo pipefail

HUB=${HUB:-piotr-voice-hub-hub-1}
CONTAINERS=${CONTAINERS:-"$HUB piotr-voice-hub-piper-1 piotr-voice-hub-speaker-recognition-1"}
HEALTH=${HEALTH:-http://localhost:8750/healthz}
INTERVAL=${SOAK_INTERVAL:-30}
RES_EVERY=${SOAK_RES_EVERY:-10}
ITERATIONS=${SOAK_ITERATIONS:-0}
MAX_DIR_MB=200
MAX_DISK_PCT=90

usage() {
  echo "usage: soak_monitor.sh log DIR START | sample DIR" >&2
  exit 2
}

mode=${1:-}
dir=${2:-}
[[ -n $mode && -n $dir ]] || usage
mkdir -p "$dir" && chmod 700 "$dir" || exit 2

note() { # one line in res.csv and on the tmux window
  echo "$1" | tee -a "$dir/res.csv" >&2
}

number() { # $1 if it is a whole number, else empty
  [[ $1 =~ ^[0-9]+$ ]] && echo "$1"
}

sample_resources() {
  local now=$1 name pid mib container dir_mb disk_pct
  for name in $CONTAINERS; do
    echo "$now,inspect,$name,$(docker inspect -f \
      '{{.State.StartedAt}},{{.RestartCount}},{{.State.Health.Status}}' "$name" 2>&1 \
      | head -1)" >>"$dir/res.csv"
    echo "$now,stats,$name,$(docker stats --no-stream --format '{{.MemUsage}},{{.CPUPerc}}' \
      "$name" 2>&1 | head -1)" >>"$dir/res.csv"
  done
  while IFS=', ' read -r pid mib; do
    container=$(grep -o -m1 -E '[0-9a-f]{64}' "/proc/$pid/cgroup" 2>/dev/null | cut -c1-12)
    echo "$now,gpu,$pid,${container:-host},$mib" >>"$dir/res.csv"
  done < <(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits \
    2>&1 || echo "error,nvidia-smi failed")
  dir_mb=$(number "$(du -sm "$dir" 2>/dev/null | cut -f1)")
  disk_pct=$(number "$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc 0-9)")
  [[ -n $dir_mb ]] || note "$now,missing,du failed; dir size counted as 0"
  [[ -n $disk_pct ]] || note "$now,missing,df failed; disk use counted as 0"
  dir_mb=${dir_mb:-0}
  disk_pct=${disk_pct:-0}
  echo "$now,disk,$dir_mb MB,$disk_pct%" >>"$dir/res.csv"
  if ((dir_mb > MAX_DIR_MB || disk_pct > MAX_DISK_PCT)); then
    echo "$now ABORT disk: dir $dir_mb MB, / $disk_pct %" | tee "$dir/ABORT" >&2
    tmux kill-session -t "${SOAK_TMUX:-t030}" 2>/dev/null # our monitors only, never the hub
    exit 3
  fi
}

case $mode in
log)
  start=${3:-}
  [[ -n $start ]] || usage
  # Re-run from START after a container restart: the file is rewritten whole, so it neither
  # duplicates nor loses lines while the json-file rotation still holds them.
  while true; do
    docker logs -f --since "$start" "$HUB" >"$dir/hub.log" 2>&1
    sleep 10
  done
  ;;
sample)
  tick=0
  while ((ITERATIONS == 0 || tick < ITERATIONS)); do
    now=$(date -u +%FT%TZ)
    body=$(curl -s -m5 "$HEALTH" 2>/dev/null | tr -d '\n')
    echo "{\"ts\":\"$now\",\"healthz\":${body:-null}}" >>"$dir/health.jsonl"
    if ((tick % RES_EVERY == 0)); then
      sample_resources "$now"
    fi
    tick=$((tick + 1))
    sleep "$INTERVAL"
  done
  ;;
*)
  usage
  ;;
esac
