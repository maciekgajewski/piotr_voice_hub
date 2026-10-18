#!/bin/bash
# T-013: live wake test on piotr's GPU, inside the CUDA 12.8 cuDNN image (no host cuBLAS).
# Usage: tools/wake-test.sh CONFIG [--satellite ID ...] [--log-level LEVEL]
# CONFIG is a hub config (config/hub.example.yaml format) with the satellites' api_key values.
# Ctrl-C to stop.
set -euo pipefail

if [[ $# -lt 1 || ! -f "$1" ]]; then
    echo "usage: $0 CONFIG [--satellite ID ...] [--log-level LEVEL]" >&2
    exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
config=$(realpath "$1")
shift
cache="${HOME}/.cache/piotr_voice_hub"
image=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
mkdir -p "$cache/huggingface" "$cache/uv" "$cache/uv-python"
tty=()
[[ -t 0 && -t 1 ]] && tty=(-it)

# --network host: the satellites are on the LAN and addressed by mDNS .local names.
exec docker run --rm "${tty[@]}" --init --gpus all --network host --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e HF_HOME=/hf -e UV_CACHE_DIR=/uv-cache -e PYTHONUNBUFFERED=1 \
    -e UV_PYTHON_INSTALL_DIR=/uv-python -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    -v /etc/localtime:/etc/localtime:ro \
    -v "$cache/huggingface:/hf" -v "$cache/uv:/uv-cache" -v "$cache/uv-python:/uv-python" \
    -v "$(command -v uv):/usr/local/bin/uv:ro" \
    -v "$repo:/repo:ro" -v "$config:/config/hub.yaml:ro" -w /repo \
    "$image" uv run --quiet --locked --no-editable --reinstall-package piotr-voice-hub \
    piotr-voice-hub-wake-test --config /config/hub.yaml "$@"
