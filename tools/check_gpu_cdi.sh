#!/bin/bash
# T-024: read-only check that a container still has GPU majors 195 and 511 and that
# a new process inside it can init CUDA.
# Usage: tools/check_gpu_cdi.sh <container> [container...]
# Does not start, stop, recreate, or print compose config.
set -uo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <container> [container...]" >&2
    exit 2
fi

# systemd prints "char-195:* rwm", "char-195:0 rwm", or sometimes "195:*".
# A minor of another device (":195") does not count as major 195.
major_ok() {
    local text=$1 major=$2
    grep -Eq "(^|[^0-9:])char-${major}([^0-9]|$)|(^|[^0-9:])${major}:[0-9*]+([^0-9]|$)" <<<"$text"
}

# Hub image: CTranslate2 (faster-whisper). Piper image: onnxruntime-gpu, no CTranslate2.
cuda_init() {
    local name=$1
    if docker exec "$name" python -c 'import ctranslate2' </dev/null >/dev/null 2>&1; then
        docker exec "$name" python -c 'import ctranslate2; n = ctranslate2.get_cuda_device_count(); print(n); raise SystemExit(0 if n > 0 else 1)' </dev/null
        return $?
    fi
    if docker exec "$name" python -c 'import onnxruntime' </dev/null >/dev/null 2>&1; then
        docker exec "$name" python -c 'import numpy as np, onnxruntime as ort; v = ort.OrtValue.ortvalue_from_numpy(np.zeros(1, np.float32), "cuda", 0); print(v.device_name()); raise SystemExit(0 if v.device_name() == "cuda" else 1)' </dev/null
        return $?
    fi
    echo "FAIL ${name}: image has neither ctranslate2 nor onnxruntime" >&2
    return 1
}

fail=0
for name in "$@"; do
    echo "== ${name}"
    id=$(docker inspect -f '{{.Id}}' "$name") || {
        echo "FAIL ${name}: docker inspect" >&2
        fail=1
        continue
    }
    unit="docker-${id}.scope"
    state=$(systemctl show "$unit" -p LoadState --value --no-pager) || state=error
    if [[ "$state" != "loaded" ]]; then
        echo "FAIL ${name}: ${unit} LoadState=${state}" >&2
        fail=1
        continue
    fi
    allow=$(systemctl show "$unit" -p DeviceAllow --no-pager) || allow=
    echo "-- ${unit}"
    printf '%s\n' "$allow"
    if ! major_ok "$allow" 195; then
        echo "FAIL ${name}: DeviceAllow has no major 195" >&2
        fail=1
    fi
    if ! major_ok "$allow" 511; then
        echo "FAIL ${name}: DeviceAllow has no major 511" >&2
        fail=1
    fi
    if ! docker exec "$name" nvidia-smi </dev/null; then
        echo "FAIL ${name}: nvidia-smi" >&2
        fail=1
    fi
    if ! cuda_init "$name"; then
        echo "FAIL ${name}: CUDA init" >&2
        fail=1
    fi
done

exit "$fail"
