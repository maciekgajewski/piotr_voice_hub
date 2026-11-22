#!/usr/bin/env bash
# Validate the satellite firmware YAML with ESPHome.
# Default: `esphome config`. Pass `--compile` for `esphome compile` (several
# minutes per device, even with a warm cache). Do not add --compile to CI.
# Uses a privately tagged image. Does not flash, does not touch production
# containers, and never reads a real secrets.yaml.
set -euo pipefail

IMAGE="piotr-voice-hub-esphome-t027:2026.4.0"
UPSTREAM="ghcr.io/esphome/esphome:2026.4.0"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/firmware/esphome"
DEVICES=(voice-pe-02.yaml voice-pe-03.yaml voice-pe-bedroom.yaml box3-satellite.yaml)
MIN_FREE_KB=$((10 * 1024 * 1024))
COMPILE=0

usage() {
  cat <<'USAGE'
Usage: tools/check_firmware.sh [--compile]

  (default)  esphome config for the four device YAML files
  --compile  esphome compile instead (slow; not for CI)

Build files go under ~/probe/piotr-voice-hub-esphome (or $PIOTR_ESPHOME_WORKDIR),
never in the repo. Dummy secrets only. Does not flash.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    --compile) COMPILE=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "unknown argument: $arg" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -e "$SRC/secrets.yaml" ]]; then
  echo "refusing: $SRC/secrets.yaml is present; this check uses only secrets.example.yaml" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "pulling $UPSTREAM and tagging as $IMAGE"
  docker pull "$UPSTREAM"
  docker tag "$UPSTREAM" "$IMAGE"
fi

free_kb() { df -P "$HOME" | awk 'NR==2{print $4}'; }

check_disk() {
  local avail
  avail="$(free_kb)"
  if (( avail < MIN_FREE_KB )); then
    echo "STOP: free space under 10 GB (${avail} KB available on \$HOME). Not compiling." >&2
    df -h "$HOME" >&2
    exit 2
  fi
}

ACTION="config"
WORKDIR_MODE="temp"
if [[ "$COMPILE" -eq 1 ]]; then
  ACTION="compile"
  WORKDIR_MODE="probe"
  check_disk
fi

if [[ "$WORKDIR_MODE" == "probe" ]]; then
  workdir="${PIOTR_ESPHOME_WORKDIR:-$HOME/probe/piotr-voice-hub-esphome}"
  mkdir -p "$workdir/cache"
  rm -rf "$workdir/src"
  cleanup() { rm -rf "$workdir/src"; }
else
  workdir="$(mktemp -d /tmp/piotr-voice-hub-fw-XXXXXX)"
  cleanup() { rm -rf "$workdir"; }
fi
trap cleanup EXIT

cp -a "$SRC/." "$workdir/src/"
# Throwaway dummy secrets for validation only: valid 32-byte base64 keys, never printed,
# never copied from a real secrets.yaml, deleted with the workdir.
python3 - "$SRC/secrets.example.yaml" "$workdir/src/secrets.yaml" <<'PY'
import base64
import sys
from pathlib import Path

example, dest = Path(sys.argv[1]), Path(sys.argv[2])
# 32 zero bytes, valid ESPHome API key format; not a real secret.
dummy_key = base64.b64encode(b"\0" * 32).decode()
text = example.read_text(encoding="utf-8")
text = text.replace("GENERATE_A_32_BYTE_BASE64_KEY", dummy_key)
text = text.replace("GENERATE_A_LONG_RANDOM_PASSWORD", "dummy-ota-password-for-config-only")
dest.write_text(text, encoding="utf-8")
PY

redact_log() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
# Drop any dummy key material if the log echoed the resolved YAML.
redacted = text.replace("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "<dummy-key>")
redacted = redacted.replace("dummy-ota-password-for-config-only", "<dummy-password>")
# Prefer the Failed config section when present.
idx = redacted.find("Failed config")
sys.stderr.write(redacted[idx:] if idx >= 0 else redacted[-4000:])
PY
}

report_compile() {
  local device="$1" log="$2"
  python3 - "$workdir/src" "$device" "$log" <<'PY'
import re
import sys
from pathlib import Path

src, device, log_path = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
name = None
for line in (src / device).read_text(encoding="utf-8").splitlines():
    if line.startswith("  name:"):
        name = line.split(":", 1)[1].strip()
        break
if not name:
    print("  (could not read device name for size report)", file=sys.stderr)
    sys.exit(0)
build = src / ".esphome" / "build" / name
app = None
csv = build / "partitions.csv"
if csv.is_file():
    for raw in csv.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 5 and parts[0] in {"app0", "ota_0"}:
            try:
                app = int(parts[4], 0)
            except ValueError:
                app = None
            break
bin_path = build / ".pioenvs" / name / "firmware.bin"
factory = build / ".pioenvs" / name / "firmware.factory.bin"
for label, path in (("firmware.bin", bin_path), ("firmware.factory.bin", factory)):
    if not path.is_file():
        continue
    size = path.stat().st_size
    if app:
        pct = 100.0 * size / app
        print(f"  {label}: {size} bytes ({pct:.1f}% of app0 {app} bytes / 0x{app:X})")
    else:
        print(f"  {label}: {size} bytes")
text = re.sub(r"\x1b\[[0-9;]*m", "", log_path.read_text(encoding="utf-8", errors="replace"))
for line in text.splitlines():
    if re.match(r"\s*(RAM|Flash):", line):
        print(f"  {line.strip()}")
PY
}

status=0
for device in "${DEVICES[@]}"; do
  echo "==== esphome $ACTION $device ===="
  log="$workdir/$device.$ACTION.log"
  if [[ "$COMPILE" -eq 1 ]]; then
    check_disk
  fi
  docker_opts=(
    run --rm --name "piotr-voice-hub-esphome-t027-$ACTION"
    --user "$(id -u):$(id -g)"
    -v "$workdir/src:/config"
    -v "$workdir/cache:/cache"
    -e ESPHOME_CACHE_DIR=/cache
    -e HOME=/cache
  )
  if [[ "$COMPILE" -eq 1 ]]; then
    docker_opts+=(--cpus=4)
    docker_opts+=(-e PLATFORMIO_CORE_DIR=/cache/.platformio)
    docker_opts+=(-e CMAKE_BUILD_PARALLEL_LEVEL=4)
    docker_opts+=(-e MAKEFLAGS=-j4)
  fi
  start=$(date +%s)
  # Writable /config and /cache so ESPHome can clone external_components.
  # Not the repo tree. No host networking, no production ports.
  if docker "${docker_opts[@]}" "$IMAGE" "$ACTION" "/config/$device" >"$log" 2>&1; then
    end=$(date +%s)
    echo "OK: $device ($((end - start))s)"
    if [[ "$COMPILE" -eq 1 ]]; then
      report_compile "$device" "$log"
    fi
  else
    end=$(date +%s)
    echo "FAILED: $device after $((end - start))s" >&2
    # Show the failure, not the resolved config (it includes dummy secrets).
    redact_log "$log"
    status=1
  fi
done
exit "$status"
