"""T-003: faster-whisper on the GPU. Prints load/warm-up/latency and VRAM; fails loudly if the
transcript lacks the expected words or the model does not appear to be on the GPU."""

import argparse
import re
import statistics
import sys
import time
import unicodedata

from faster_whisper import WhisperModel, decode_audio
from gpu_info import GpuMemorySampler

SAMPLE_RATE = 16000
RUNS = 5
# large-v3-turbo fp16 is about 1.6 GB of weights; less than this means it is not on the GPU.
MIN_MODEL_MIB = 1000
# Words in the documented clip (audio/monitor-test/box3-tts-20260526T110342Z.wav).
DEFAULT_EXPECTED_WORDS = "test,pierwszy"


def normalize(text: str) -> list[str]:
    """Lowercase, strip diacritics and punctuation, split into words."""
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^\w\s]", " ", plain).split()


def transcribe(model: WhisperModel, audio, beam_size: int) -> tuple[float, str]:
    start = time.perf_counter()
    segments, _ = model.transcribe(audio, language="pl", beam_size=beam_size)
    text = " ".join(s.text.strip() for s in segments)  # segments is lazy; this runs the decode
    return time.perf_counter() - start, text


def fail(message: str) -> int:
    print(f"WHISPER SMOKE FAILED: {message}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("clip")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument(
        "--expect-words",
        default=DEFAULT_EXPECTED_WORDS,
        help="comma-separated words the transcript must contain (case/diacritics-insensitive)",
    )
    args = parser.parse_args()
    expected = [w for word in args.expect_words.split(",") for w in normalize(word)]
    assert expected, "--expect-words is empty"

    sampler = GpuMemorySampler()
    print(f"GPU: {sampler.describe()}")
    print(f"VRAM used before load: {sampler.baseline_mib} MiB")
    sampler.start()

    audio = decode_audio(args.clip, sampling_rate=SAMPLE_RATE)
    print(f"Clip: {args.clip}, {len(audio) / SAMPLE_RATE:.2f} s")

    start = time.perf_counter()
    model = WhisperModel(args.model, device="cuda", compute_type=args.compute_type)
    print(f"Model {args.model} {args.compute_type} load: {time.perf_counter() - start:.2f} s")
    loaded_mib = sampler.used_mib()
    model_mib = loaded_mib - sampler.baseline_mib
    if model_mib < MIN_MODEL_MIB:
        return fail(f"only +{model_mib} MiB after load; the model does not seem to be on the GPU")

    warmup, text = transcribe(model, audio, beam_size=5)
    print(f"Warm-up (first) transcription, beam 5: {warmup:.2f} s -> {text!r}")
    missing = [w for w in expected if w not in normalize(text)]
    if missing:
        return fail(f"transcript {text!r} lacks expected word(s) {missing}")

    for beam in (5, 1):
        times = [transcribe(model, audio, beam)[0] for _ in range(RUNS)]
        print(
            f"Beam {beam}, {RUNS} runs: median {statistics.median(times):.3f} s, "
            f"min {min(times):.3f} s, max {max(times):.3f} s"
        )

    sampler.stop()
    print(
        f"VRAM: loaded {loaded_mib} MiB, peak {sampler.peak_mib} MiB "
        f"(this process incl. CUDA context: +{model_mib} MiB loaded, "
        f"+{sampler.peak_mib - sampler.baseline_mib} MiB peak)"
    )
    print("WHISPER SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
