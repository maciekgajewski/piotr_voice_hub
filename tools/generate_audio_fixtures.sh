#!/bin/bash
# T-028: start a throwaway Piper, generate fixtures, then remove only our container and tag.
# Never uses the production piper (:latest or port 10201). Bind is 127.0.0.1:10228 only.
# Usage (from the repo root on piotr): tools/generate_audio_fixtures.sh
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

IMAGE=piotr-voice-hub-piper:t028
# Re-tag a named existing image, never :latest.
SOURCE_TAG=${SOURCE_TAG:-piotr-voice-hub-piper:rollback-4c96b16}
NAME=piotr-voice-hub-piper-t028
PORT=10228
URI=tcp://127.0.0.1:${PORT}
export PATH="${HOME}/.local/bin:${PATH}"

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "removing leftover $NAME" >&2
    docker rm -f "$NAME"
fi

if ! docker image inspect "$SOURCE_TAG" >/dev/null 2>&1; then
    echo "source image $SOURCE_TAG is not present; set SOURCE_TAG to a named local piper image" >&2
    exit 1
fi

docker tag "$SOURCE_TAG" "$IMAGE"

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker rmi "$IMAGE" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Prefer CDI (T-024). Fall back to --gpus all, the same flag as tools/stt-gpu-test.sh.
run_piper() {
    local gpu_args=("$@")
    docker run -d --name "$NAME" \
        "${gpu_args[@]}" \
        -p 127.0.0.1:${PORT}:10228 \
        "$IMAGE" \
        --uri "tcp://0.0.0.0:${PORT}" --voice pl_PL-bass-high --data-dir /opt/piper/voices
}

if ! run_piper --device=nvidia.com/gpu=all; then
    echo "CDI device flag failed; retrying with --gpus all" >&2
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    run_piper --gpus all
fi

echo "waiting for Piper on $URI"
for i in $(seq 1 90); do
    if docker exec "$NAME" python -m wyoming_piper.health_check --uri "tcp://127.0.0.1:${PORT}" --timeout 5 >/dev/null 2>&1; then
        echo "Piper healthy after ${i}s"
        break
    fi
    if [[ "$i" -eq 90 ]]; then
        echo "Piper did not become healthy" >&2
        docker logs "$NAME" >&2 || true
        exit 1
    fi
    sleep 2
done

uv run --locked python tools/generate_audio_fixtures.py --piper-uri "$URI"
