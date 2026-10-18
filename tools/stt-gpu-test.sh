#!/bin/bash
# T-008: run the GPU STT integration test inside the CUDA 12.8 cuDNN image on this host's GPU.
# Usage: tools/stt-gpu-test.sh
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cache="${HOME}/.cache/piotr_voice_hub"
image=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
mkdir -p "$cache/huggingface" "$cache/uv" "$cache/uv-python"

docker run --rm --gpus all --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e PIOTR_VOICE_HUB_GPU_TESTS=1 -e HF_HOME=/hf -e UV_CACHE_DIR=/uv-cache \
    -e UV_PYTHON_INSTALL_DIR=/uv-python -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    -v "$cache/huggingface:/hf" -v "$cache/uv:/uv-cache" -v "$cache/uv-python:/uv-python" \
    -v "$(command -v uv):/usr/local/bin/uv:ro" \
    -v "$repo:/repo:ro" -w /repo \
    "$image" uv run --locked --no-editable --reinstall-package piotr-voice-hub pytest -p no:cacheprovider -s -m gpu tests/test_stt_gpu.py
