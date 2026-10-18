#!/bin/bash
# T-003: build the GPU smoke image and run the checks on this host's GPU.
# Usage: tools/gpu-smoke.sh <polish-speech-clip.(wav|flac)> [expected,words]
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "usage: $0 <polish-speech-clip.(wav|flac)> [expected,words]" >&2
    exit 2
fi

clip=$(realpath "$1")
expect=("${@:2}")
repo=$(cd "$(dirname "$0")/.." && pwd)
image=piotr-voice-hub-gpu-smoke
cache="${HOME}/.cache/piotr_voice_hub"
voice=pl_PL-bass-high
voice_url="https://huggingface.co/rhasspy/piper-voices/resolve/main/pl/pl_PL/bass/high"
mkdir -p "$cache/huggingface" "$cache/piper"
for file in "$voice.onnx" "$voice.onnx.json"; do
    [[ -f "$cache/piper/$file" ]] || curl -fsSL -o "$cache/piper/$file" "$voice_url/$file"
done

docker build -f "$repo/docker/gpu-smoke.Dockerfile" -t "$image" "$repo/tools/gpu_smoke"

run() {
    docker run --rm --gpus all --user "$(id -u):$(id -g)" \
        -e HF_HOME=/hf -v "$cache/huggingface:/hf" \
        -v "$cache/piper:/piper:ro" \
        -v "$clip:/clip/$(basename "$clip"):ro" \
        "$image" "$@"
}

run /opt/whisper/bin/python whisper_smoke.py "/clip/$(basename "$clip")" \
    ${expect[0]:+--expect-words "${expect[0]}"}
run /opt/ort/bin/python ort_smoke.py
run /opt/ort/bin/python piper_vram.py "/piper/$voice.onnx"
