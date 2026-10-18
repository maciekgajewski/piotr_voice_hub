"""T-003: VRAM of the Piper Polish voice on onnxruntime-gpu (CUDA EP), as wyoming-piper uses it.

Feeds random phoneme ids (no phonemizer needed): memory depends on input length, not content.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
from gpu_info import GpuMemorySampler

CUDA = "CUDAExecutionProvider"
RUNS = 5
PHONEMES = 400  # roughly a 2-3 sentence reply


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    voice = json.loads(Path(f"{args.model}.json").read_text(encoding="utf-8"))
    num_symbols = int(voice["num_symbols"])
    sample_rate = int(voice["audio"]["sample_rate"])

    sampler = GpuMemorySampler()
    print(f"GPU: {sampler.describe()}")
    print(f"VRAM used before session: {sampler.baseline_mib} MiB")
    sampler.start()

    start = time.perf_counter()
    session = ort.InferenceSession(str(args.model), providers=[CUDA])
    print(f"Session load: {time.perf_counter() - start:.2f} s, providers {session.get_providers()}")
    if session.get_providers()[0] != CUDA:
        print("PIPER VRAM FAILED: CUDA EP not active")
        return 1
    loaded_mib = sampler.used_mib()

    rng = np.random.default_rng(0)
    feeds: dict[str, np.ndarray] = {
        "input": rng.integers(1, num_symbols, size=(1, PHONEMES), dtype=np.int64),
        "input_lengths": np.array([PHONEMES], dtype=np.int64),
        "scales": np.array([0.667, 1.0, 0.8], dtype=np.float32),
    }
    names = {i.name for i in session.get_inputs()}
    if "sid" in names:
        feeds["sid"] = np.array([0], dtype=np.int64)
    assert set(feeds) == names, f"unexpected model inputs {names}"

    for run in range(RUNS):
        start = time.perf_counter()
        (audio,) = session.run(None, feeds)
        elapsed = time.perf_counter() - start
        seconds = audio.size / sample_rate
        print(f"Run {run + 1}: {elapsed:.3f} s for {seconds:.2f} s of audio")
    sampler.stop()
    print(
        f"VRAM: loaded {loaded_mib} MiB, peak {sampler.peak_mib} MiB "
        f"(this process: +{loaded_mib - sampler.baseline_mib} MiB loaded, "
        f"+{sampler.peak_mib - sampler.baseline_mib} MiB peak)"
    )
    print("PIPER VRAM OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
