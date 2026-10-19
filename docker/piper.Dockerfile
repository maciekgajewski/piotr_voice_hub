# T-015: the hub's own wyoming-piper on the GPU (design.md §9). Build context: the repo root
# (docker-compose.yml, service `piper`). Same pinned CUDA 12.8 + cuDNN 9 base as the hub; the
# only onnxruntime is onnxruntime-gpu 1.26.0 (the newest CUDA 12 build, T-003).
# Licences: the image contains GPL-3.0 components (piper-tts and the espeak-ng it bundles), so
# it is for local builds only and must not be published to a registry without GPL compliance.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04@sha256:ac55d124da4882b497f732d8dfd9a702d5447a5f29d08d56da6f64f0a1eb34bc

COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    PATH=/opt/piper/venv/bin:$PATH

RUN uv python install 3.12 && uv venv --python 3.12 /opt/piper/venv

# Hash-pinned, and --no-deps: piper-tts depends on the CPU onnxruntime, which requirements.txt
# leaves out (docker/piper/requirements.in). The install check below fails the build if it is
# there anyway, or if onnxruntime-gpu is not 1.26.0.
COPY docker/piper/requirements.txt /opt/piper/requirements.txt
RUN uv pip install --python /opt/piper/venv --require-hashes --no-deps \
        -r /opt/piper/requirements.txt

# The voice, pinned by revision and SHA-256 (rhasspy/piper-voices, main on 2026-10-19; the
# same files T-003 measured). Never downloaded at runtime.
# The directory first: ADD --chmod would also apply 644 to a directory it creates.
RUN mkdir -m 755 /opt/piper/voices
ARG VOICES=https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/pl/pl_PL/bass/high
ADD --chmod=644 --checksum=sha256:73b8408967c58118700f21eb2413cd8b666c7844c6136cf674bf5dd56bde72c2 \
    ${VOICES}/pl_PL-bass-high.onnx /opt/piper/voices/pl_PL-bass-high.onnx
ADD --chmod=644 --checksum=sha256:7bb41aa14fee87a31cc32264119c09e3553335196d3db15b39b1c18790e13c59 \
    ${VOICES}/pl_PL-bass-high.onnx.json /opt/piper/voices/pl_PL-bass-high.onnx.json

COPY src/piotr_voice_hub/tts/piper_service.py docker/piper/entrypoint.py /opt/piper/
RUN python /opt/piper/entrypoint.py --check-install

WORKDIR /tmp
USER 65534:65534
ENTRYPOINT ["python", "/opt/piper/entrypoint.py"]
# Loopback only: the hub (host network) is the only client, and wyoming reads any message size
# a client announces, so the LAN must not reach it (PR #18 review B2).
CMD ["--uri", "tcp://127.0.0.1:10201", "--voice", "pl_PL-bass-high", "--data-dir", "/opt/piper/voices"]
