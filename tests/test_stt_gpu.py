"""Optional GPU integration test: real large-v3-turbo on CUDA.

Opt-in: runs only with PIOTR_VOICE_HUB_GPU_TESTS=1 and a CUDA device, so `make check` never
downloads the 1.6 GB model or needs cuBLAS/cuDNN on the host. Run it on piotr with
tools/stt-gpu-test.sh (inside the CUDA 12.8 cuDNN image), which also reports peak VRAM.
"""

import asyncio
import os
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from piotr_voice_hub.config import SttConfig, SttPartialConfig
from piotr_voice_hub.stt.whisper import BYTES_PER_SECOND, FasterWhisperSpeechToText, TextEnd

ctranslate2 = pytest.importorskip("ctranslate2")
pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.environ.get("PIOTR_VOICE_HUB_GPU_TESTS") != "1", reason="PIOTR_VOICE_HUB_GPU_TESTS != 1"
    ),
    pytest.mark.skipif(ctranslate2.get_cuda_device_count() == 0, reason="no CUDA device"),
]

DATA = Path(__file__).parent / "data"
CHUNK_BYTES = 1024  # 32 ms, the satellite frame size
MAX_AUDIO_SECONDS = 16.0  # max_segment_seconds 15 + 1 s pre-roll
# design.md §7 stt section
CONFIG = SttConfig(
    model="large-v3-turbo",
    device="cuda",
    compute_type="float16",
    beam_size=5,
    partial=SttPartialConfig(interval_seconds=0.75, window_seconds=4.0, beam_size=1),
)


def read_pcm(name: str) -> bytes:
    with wave.open(str(DATA / name), "rb") as wav:
        assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels()) == (16000, 2, 1)
        return wav.readframes(wav.getnframes())


def noise(seconds: float, sigma: float) -> bytes:
    samples = np.random.default_rng(0).normal(0, sigma, int(seconds * 16000))
    return samples.astype("<i2").tobytes()


CLIPS = {
    "speech": read_pcm("speech_pl_16k.wav"),
    "wake": read_pcm("wake_pl_16k.wav"),
    "noise": noise(3.0, 300),
    "silence": bytes(3 * BYTES_PER_SECOND),
}


def words(text: str) -> list[str]:
    return [w.strip(".,!?").lower() for w in text.split()]


async def stream(
    stt: FasterWhisperSpeechToText, name: str, pcm: bytes
) -> tuple[list[str], str, float]:
    """Send audio at real-time pace; return partial texts, final text, final latency."""
    session = stt.create_streaming_session(name, MAX_AUDIO_SECONDS)
    for start in range(0, len(pcm), CHUNK_BYTES):
        await session.send_audio(pcm[start : start + CHUNK_BYTES])
        await asyncio.sleep(CHUNK_BYTES / BYTES_PER_SECOND)
    started_at = time.monotonic()
    final = await session.transcribe_final()
    latency = time.monotonic() - started_at
    partials = []
    while not isinstance(event := await session.receive_text(), TextEnd):
        partials.append(event.text)
    print(f"  {name}: partials={partials} final={final!r} final_latency={latency:.3f} s")
    return partials, final, latency


def check(results: dict[str, tuple[list[str], str, float]]) -> None:
    partials, final, _ = results["speech"]
    assert partials
    assert {"test", "pierwszy"} <= set(words(final))
    partials, final, _ = results["wake"]
    assert words(final)[:5] == ["ryszardzie", "jaka", "będzie", "jutro", "pogoda"]
    for name in ("noise", "silence"):
        partials, final, _ = results[name]
        texts = [*partials, final]
        # No bias: non-speech never yields the wake word (it did with hotwords; T-008 log).
        assert not any("ryszard" in w for t in texts for w in words(t)), (name, texts)


def test_real_model_one_stream_then_four_concurrent_streams() -> None:
    async def run() -> None:
        stt = FasterWhisperSpeechToText(CONFIG)
        started_at = time.monotonic()
        await stt.start()
        print(f"\nload+warm-up {time.monotonic() - started_at:.2f} s\none stream at a time:")
        check({name: await stream(stt, name, pcm) for name, pcm in CLIPS.items()})

        print("four concurrent streams (one per clip, like four satellites):")
        results = await asyncio.gather(*(stream(stt, n, p) for n, p in CLIPS.items()))
        check(dict(zip(CLIPS, results, strict=True)))
        print(f"max final latency, 4 streams: {max(r[2] for r in results):.3f} s")

    asyncio.run(run())
