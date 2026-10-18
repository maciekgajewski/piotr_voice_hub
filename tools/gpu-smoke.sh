#!/bin/bash
# T-003: build the GPU smoke image and run both checks on this host's GPU.
# Usage: tools/gpu-smoke.sh <polish-speech-clip.(wav|flac)>
set -euo pipefail

clip=$(realpath "$1")
repo=$(cd "$(dirname "$0")/.." && pwd)
image=piotr-voice-hub-gpu-smoke
hf_cache="${HOME}/.cache/piotr_voice_hub/huggingface"
mkdir -p "$hf_cache"

docker build -f "$repo/docker/gpu-smoke.Dockerfile" -t "$image" "$repo"

run() {
    docker run --rm --gpus all --user "$(id -u):$(id -g)" \
        -e HF_HOME=/hf -v "$hf_cache:/hf" \
        -v "$clip:/clip/$(basename "$clip"):ro" \
        "$image" "$@"
}

run /opt/whisper/bin/python whisper_smoke.py "/clip/$(basename "$clip")"
run /opt/ort/bin/python ort_smoke.py
