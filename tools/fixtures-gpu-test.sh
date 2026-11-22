#!/bin/bash
# T-028: run the GPU fixture STT tests in a privately tagged CUDA image.
# Never uses :latest or production ports. Usage: tools/fixtures-gpu-test.sh
set -euo pipefail

if [[ $# -ne 0 ]]; then
    echo "usage: $0" >&2
    exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cache="${HOME}/.cache/piotr_voice_hub"
source_image=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
image=piotr-voice-hub-fixtures-test:t028
mkdir -p "$cache/huggingface" "$cache/uv" "$cache/uv-python"
export PATH="${HOME}/.local/bin:${PATH}"

docker tag "$source_image" "$image"
cleanup() {
    docker rmi "$image" >/dev/null 2>&1 || true
    if [[ -n "${monitor:-}" ]]; then
        kill "$monitor" 2>/dev/null || true
    fi
    rm -f "$vram_log"
}
vram_log=$(mktemp)
trap cleanup EXIT
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -lms 200 > "$vram_log" &
monitor=$!

# Same CUDA image and flags as tools/stt-gpu-test.sh; only our tag and test file differ.
docker run --rm --gpus all --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e PIOTR_VOICE_HUB_GPU_TESTS=1 -e HF_HOME=/hf -e UV_CACHE_DIR=/uv-cache \
    -e UV_PYTHON_INSTALL_DIR=/uv-python -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
    -v "$cache/huggingface:/hf" -v "$cache/uv:/uv-cache" -v "$cache/uv-python:/uv-python" \
    -v "$(command -v uv):/usr/local/bin/uv:ro" \
    -v "$repo:/repo:ro" -w /repo \
    "$image" uv run --locked --no-editable --reinstall-package piotr-voice-hub \
    pytest -p no:cacheprovider -s -m gpu tests/test_audio_fixtures_gpu.py

echo "device VRAM MiB: idle $(head -n1 "$vram_log"), peak $(sort -n "$vram_log" | tail -n1)"
