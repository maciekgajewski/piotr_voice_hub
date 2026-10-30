# T-018: the hub's speaker-recognition service on the CPU (design.md §4.3, §9). Build context:
# the repo root (docker-compose.yml, service `speaker-recognition`). No GPU: CPU-only torch
# wheels and no CUDA base, so it holds no VRAM.
# Licences: SpeechBrain and the spkrec-ecapa-voxceleb model are Apache-2.0.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_NO_CACHE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    PATH=/opt/speaker/venv/bin:$PATH

RUN uv python install 3.12 && uv venv --python 3.12 /opt/speaker/venv

COPY docker/speaker/requirements.txt /opt/speaker/requirements.txt
# The index URLs (the PyTorch CPU index first) are in requirements.txt.
RUN uv pip install --python /opt/speaker/venv --require-hashes \
        --index-strategy unsafe-best-match -r /opt/speaker/requirements.txt

# The model, pinned by revision and SHA-256 (speechbrain/spkrec-ecapa-voxceleb, main on
# 2026-10-30; the model the old service downloaded). Never downloaded at runtime.
RUN mkdir -m 755 /opt/ecapa
ARG ECAPA=https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/resolve/0f99f2d0ebe89ac095bcc5903c4dd8f72b367286
ADD --chmod=644 --checksum=sha256:6f78854fa04ba59e761437b76a2575d3aba5e5016de3e9b69f0c9a5077fb1a41 \
    ${ECAPA}/hyperparams.yaml /opt/ecapa/hyperparams.yaml
ADD --chmod=644 --checksum=sha256:0575cb64845e6b9a10db9bcb74d5ac32b326b8dc90352671d345e2ee3d0126a2 \
    ${ECAPA}/embedding_model.ckpt /opt/ecapa/embedding_model.ckpt
ADD --chmod=644 --checksum=sha256:cd70225b05b37be64fc5a95e24395d804231d43f74b2e1e5a513db7b69b34c33 \
    ${ECAPA}/mean_var_norm_emb.ckpt /opt/ecapa/mean_var_norm_emb.ckpt
ADD --chmod=644 --checksum=sha256:fd9e3634fe68bd0a427c95e354c0c677374f62b3f434e45b78599950d860d535 \
    ${ECAPA}/classifier.ckpt /opt/ecapa/classifier.ckpt
ADD --chmod=644 --checksum=sha256:e13c3a167bb4112685670ee896d20e2b565af16b3a4ceeaa8689fa4d22adb8b9 \
    ${ECAPA}/label_encoder.txt /opt/ecapa/label_encoder.txt

COPY src/piotr_voice_hub/speaker/service.py docker/speaker/entrypoint.py /opt/speaker/
# Load the model once at build time, as root: SpeechBrain links its files inside /opt/ecapa
# (e.g. label_encoder.ckpt), which the read-only runtime user cannot. Also a build-time check
# that the pinned files and libraries load and embed (offline).
RUN cd /opt/speaker && python -c "import entrypoint, service; service.warm_up(entrypoint.load_embedder())"

WORKDIR /tmp
# Never root. Compose runs it as HUB_USER (who can read the profiles); nobody otherwise.
USER 65534:65534
ENTRYPOINT ["python", "/opt/speaker/entrypoint.py"]
# Loopback only: the hub (host network) is its only client.
CMD ["--host", "127.0.0.1", "--port", "2141", "--profiles-dir", "/profiles"]
