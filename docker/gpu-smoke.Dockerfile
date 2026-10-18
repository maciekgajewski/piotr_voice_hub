# T-003: one-off GPU smoke test image (not the hub image).
# Checks that faster-whisper/CTranslate2 and onnxruntime-gpu work on this GPU (sm_120)
# on the same CUDA base image the hub will use (design.md §3, §9).
# Build context: tools/gpu_smoke (see tools/gpu-smoke.sh).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_NO_CACHE=1 \
    PYTHONUNBUFFERED=1

RUN uv python install 3.12

WORKDIR /smoke

# Two separate venvs: onnxruntime (CPU, pulled by faster-whisper) and onnxruntime-gpu
# both provide the `onnxruntime` module and must never share an environment.
COPY whisper-requirements.txt ort-requirements.txt /smoke/
RUN uv venv --python 3.12 /opt/whisper \
    && uv pip install --python /opt/whisper/bin/python --require-hashes -r whisper-requirements.txt \
    && uv venv --python 3.12 /opt/ort \
    && uv pip install --python /opt/ort/bin/python --require-hashes -r ort-requirements.txt

COPY *.py /smoke/
