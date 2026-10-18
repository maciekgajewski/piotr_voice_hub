"""T-003: faster-whisper on the GPU. Prints load/warm-up/latency and device VRAM delta."""

import argparse
import statistics
import sys
import time

from faster_whisper import WhisperModel, decode_audio
from gpu_info import GpuMemorySampler

SAMPLE_RATE = 16000
RUNS = 5


def transcribe(model: WhisperModel, audio, beam_size: int) -> tuple[float, str]:
    start = time.perf_counter()
    segments, _ = model.transcribe(audio, language="pl", beam_size=beam_size, hotwords="Ryszardzie")
    text = " ".join(s.text.strip() for s in segments)  # segments is lazy; this runs the decode
    return time.perf_counter() - start, text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute-type", default="float16")
    args = parser.parse_args()

    sampler = GpuMemorySampler()
    print(f"GPU: {sampler.describe()}")
    print(f"Device VRAM used before load: {sampler.baseline_mib} MiB")
    sampler.start()

    audio = decode_audio(args.clip, sampling_rate=SAMPLE_RATE)
    print(f"Clip: {args.clip}, {len(audio) / SAMPLE_RATE:.2f} s")

    start = time.perf_counter()
    model = WhisperModel(args.model, device="cuda", compute_type=args.compute_type)
    print(f"Model {args.model} {args.compute_type} load: {time.perf_counter() - start:.2f} s")
    loaded_mib = sampler.used_mib()

    warmup, text = transcribe(model, audio, beam_size=5)
    print(f"Warm-up (first) transcription, beam 5: {warmup:.2f} s -> {text!r}")

    for beam in (5, 1):
        times = [transcribe(model, audio, beam)[0] for _ in range(RUNS)]
        print(
            f"Beam {beam}, {RUNS} runs: median {statistics.median(times):.3f} s, "
            f"min {min(times):.3f} s, max {max(times):.3f} s"
        )

    sampler.stop()
    print(
        f"Device VRAM: loaded {loaded_mib} MiB, peak {sampler.peak_mib} MiB "
        f"(delta vs before: loaded +{loaded_mib - sampler.baseline_mib} MiB, "
        f"peak +{sampler.peak_mib - sampler.baseline_mib} MiB)"
    )
    print("WHISPER SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
