# T-012: the hub image (design.md §9). Build context: the repo root (docker-compose.yml).
# CUDA 12.8 + cuDNN 9 user space for faster-whisper/CTranslate2 on sm_120 (T-003). The only
# onnxruntime is the CPU one (Silero VAD); onnxruntime-gpu must never be installed here.
# Base images pinned by digest (multi-arch index digests, checked 2026-10-19 on piotr).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc

COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

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
# Never root. Compose runs it as HUB_USER (the config owner); nobody is the default otherwise.
USER 65534:65534
ENTRYPOINT ["piotr-voice-hub"]
CMD ["--config", "/config/hub.yaml"]
