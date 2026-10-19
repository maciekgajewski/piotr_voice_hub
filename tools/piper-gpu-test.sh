#!/bin/bash
# T-015/T-016: run the Piper integration tests (the service, and the hub's client) against
# the running `piper` service (docker compose up -d --build piper; it listens on 10201 once
# warm).
# Usage: tools/piper-gpu-test.sh [tcp://127.0.0.1:10201]
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "usage: $0 [tcp://host:port]" >&2
    exit 2
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
PIOTR_VOICE_HUB_PIPER_URI="${1:-tcp://127.0.0.1:10201}" \
    uv run --locked pytest -p no:cacheprovider -s -m gpu tests/test_piper_gpu.py tests/test_tts_gpu.py
