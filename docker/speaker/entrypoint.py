"""Entry point of the speaker-recognition image (T-018): `service.main` with the real
SpeechBrain ECAPA embedder on the CPU. Lives only in the image (docker/speaker.Dockerfile),
next to service.py."""

import sys

import numpy as np
import service
import torch
from speechbrain.inference.speaker import EncoderClassifier

# Baked into the image, pinned (docker/speaker.Dockerfile); HF_HUB_OFFLINE=1 there.
MODEL_DIR = "/opt/ecapa"


def load_embedder() -> service.Embed:
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
