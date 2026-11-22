#!/usr/bin/env bash
# Validate the satellite firmware YAML with ESPHome (`esphome config`).
# Uses a privately tagged image. Does not flash, does not touch production
# containers, and never reads a real secrets.yaml.
set -euo pipefail

IMAGE="piotr-voice-hub-esphome-t027:2026.4.0"
UPSTREAM="ghcr.io/esphome/esphome:2026.4.0"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/firmware/esphome"
DEVICES=(voice-pe-02.yaml voice-pe-03.yaml voice-pe-bedroom.yaml box3-satellite.yaml)

if [[ -e "$SRC/secrets.yaml" ]]; then
  echo "refusing: $SRC/secrets.yaml is present; this check uses only secrets.example.yaml" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "pulling $UPSTREAM and tagging as $IMAGE"
  docker pull "$UPSTREAM"
  docker tag "$UPSTREAM" "$IMAGE"
fi

workdir="$(mktemp -d /tmp/piotr-voice-hub-fw-XXXXXX)"
cleanup() { rm -rf "$workdir"; }
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

status=0
for device in "${DEVICES[@]}"; do
  echo "==== esphome config $device ===="
  log="$workdir/$device.config.log"
  # Writable /config and /cache so ESPHome can clone external_components.
  # Not the repo tree. No host networking, no production ports.
  if docker run --rm --name "piotr-voice-hub-esphome-t027-config" \
    --user "$(id -u):$(id -g)" \
    -v "$workdir/src:/config" \
    -v "$workdir/cache:/cache" \
    -e ESPHOME_CACHE_DIR=/cache \
    -e HOME=/cache \
    "$IMAGE" config "/config/$device" >"$log" 2>&1; then
    echo "OK: $device"
  else
    echo "FAILED: $device" >&2
    # Show the failure, not the resolved config (it includes dummy secrets).
    python3 - "$log" <<'PY'
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
    status=1
  fi
done
exit "$status"
