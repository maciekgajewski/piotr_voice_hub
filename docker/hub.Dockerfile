# T-012: the hub image (design.md §9). Build context: the repo root (docker-compose.yml).
# CUDA 12.8 + cuDNN 9 user space for faster-whisper/CTranslate2 on sm_120 (T-003). The only
# onnxruntime is the CPU one (Silero VAD); onnxruntime-gpu must never be installed here.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

# The container runs as the config owner's uid (compose `user:`), not root: everything it
# needs to read is world-readable, and it writes only to /hf (the model cache) and /tmp.
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HOME=/hf \
    PATH=/opt/venv/bin:$PATH

RUN uv python install 3.12

WORKDIR /src
# Dependencies first (cached while only the hub's code changes), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && rm -rf /src

WORKDIR /
ENTRYPOINT ["piotr-voice-hub"]
CMD ["--config", "/config/hub.yaml"]
