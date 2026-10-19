"""Entry point of the Piper image (T-015): `piper_service.main` with the real piper-tts and
wyoming-piper. Lives only in the image (docker/piper.Dockerfile), next to piper_service.py."""

import io
import json
import sys
import wave
from pathlib import Path
from typing import Any

import piper_service
import wyoming_piper.__main__ as wyoming_main
import wyoming_piper.handler as wyoming_handler
from piper import PiperConfig, PiperVoice


def load_voice(config: Path, session: Any) -> PiperVoice:
    """What `PiperVoice.load` does, but with our checked CUDA session."""
    with open(config, encoding="utf-8") as config_file:
        return PiperVoice(session=session, config=PiperConfig.from_dict(json.load(config_file)))


def synthesize(voice: PiperVoice, text: str) -> float:
    """Seconds of audio `text` gives, synthesized the way wyoming-piper does (synthesize_wav)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        voice.synthesize_wav(text, writer)
    buffer.seek(0)
    with wave.open(buffer, "rb") as reader:
        return reader.getnframes() / reader.getframerate()


if __name__ == "__main__":
    sys.exit(
        piper_service.main(
            sys.argv[1:],
            load_voice=load_voice,
            synthesize=synthesize,
            handler=wyoming_handler,
            server=wyoming_main,
            serve=wyoming_main.run,
        )
    )
