"""Entry point of the speaker-recognition image (T-018): `service.main` with the real
SpeechBrain ECAPA embedder on the CPU. Lives only in the image (docker/speaker.Dockerfile),
next to service.py."""

import logging
import sys

import numpy as np
import service
import torch
from speechbrain.inference.speaker import EncoderClassifier

# Baked into the image, pinned (docker/speaker.Dockerfile); HF_HUB_OFFLINE=1 there.
MODEL_DIR = "/opt/ecapa"
# Torch would use every core (12 threads on the Ryzen 5 5600). Two keep a long request under
# about half a second (T-018 log) and leave the other four cores to the hub (Silero VAD,
# Whisper's CPU side, the API), Ollama and the other services on piotr (PR #27 review N5).
TORCH_THREADS = 2


def load_embedder() -> service.Embed:
    torch.set_num_threads(TORCH_THREADS)
    torch.set_num_interop_threads(1)  # one request at a time: no parallel ops to overlap
    logging.getLogger("speaker_service").info("torch threads: %d", torch.get_num_threads())
    classifier = EncoderClassifier.from_hparams(
        source=MODEL_DIR,
        savedir=MODEL_DIR,
        run_opts={"device": "cpu"},
        # hyperparams.yaml fetches its checkpoints from the Hub repo name; read them here.
        overrides={"pretrained_path": MODEL_DIR},
    )
    assert classifier is not None

    def embed(samples: np.ndarray) -> np.ndarray:
        with torch.inference_mode():
            embedding = classifier.encode_batch(torch.from_numpy(samples).unsqueeze(0))
        return embedding.squeeze().numpy()

    return embed


if __name__ == "__main__":
    sys.exit(service.main(sys.argv[1:], load_embedder))
