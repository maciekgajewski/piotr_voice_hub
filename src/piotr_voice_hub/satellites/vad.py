"""Silero VAD speech segmentation with fixed hysteresis (design.md §4.1 step 2).

One shared CPU onnxruntime session; one `SileroVad` (recurrent state + hysteresis) per stream.
Input is 16 kHz 16-bit mono PCM in frames of 512 samples (32 ms).
"""

import math
from enum import Enum
from pathlib import Path

import numpy as np
import onnxruntime as ort

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
CONTEXT_SAMPLES = 64  # Silero v5+ expects the previous frame's last 64 samples prepended
START_THRESHOLD = 0.5
START_SECONDS = 0.25
END_THRESHOLD = 0.35
MODEL_PATH = Path(__file__).with_name("silero_vad.onnx")  # silero-vad v6.2.3, MIT


class VadTransition(Enum):
    SPEECH_STARTED = "speech_started"
    SPEECH_ENDED = "speech_ended"


def _frames(seconds: float) -> int:
    return math.ceil(round(seconds / FRAME_SECONDS, 6))


class Hysteresis:
    """Start when p >= 0.5 for 250 ms; end after `end_silence_seconds` with p < 0.35."""

    def __init__(self, end_silence_seconds: float) -> None:
        assert end_silence_seconds > 0, end_silence_seconds
        self._start_frames = _frames(START_SECONDS)
        self._end_frames = _frames(end_silence_seconds)
        self._run = 0  # consecutive qualifying frames towards the next transition
        self.speaking = False

    def update(self, probability: float) -> VadTransition | None:
        assert 0.0 <= probability <= 1.0, probability
        if not self.speaking:
            self._run = self._run + 1 if probability >= START_THRESHOLD else 0
            if self._run >= self._start_frames:
                self.speaking, self._run = True, 0
                return VadTransition.SPEECH_STARTED
            return None
        self._run = self._run + 1 if probability < END_THRESHOLD else 0
        if self._run >= self._end_frames:
            self.speaking, self._run = False, 0
            return VadTransition.SPEECH_ENDED
        return None


def load_session(path: Path = MODEL_PATH) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


class SileroVad:
    """Per-stream Silero state plus hysteresis. Not thread-safe; one instance per stream."""

    def __init__(self, session: ort.InferenceSession, end_silence_seconds: float) -> None:
        self._session = session
        self._hysteresis = Hysteresis(end_silence_seconds)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        self._sample_rate = np.array(SAMPLE_RATE, dtype=np.int64)

    @property
    def speaking(self) -> bool:
        return self._hysteresis.speaking

    def process_frame(self, pcm: bytes) -> tuple[float, VadTransition | None]:
        """Process exactly one 32 ms frame; return its speech probability and any transition."""
        assert len(pcm) == FRAME_BYTES, f"VAD frame must be {FRAME_BYTES} bytes, got {len(pcm)}"
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32).reshape(1, -1) / 32768.0
        window = np.concatenate([self._context, samples], axis=1)
        output, self._state = self._session.run(
            None, {"input": window, "state": self._state, "sr": self._sample_rate}
        )
        self._context = window[:, -CONTEXT_SAMPLES:]
        probability = float(np.asarray(output).reshape(-1)[0])
        return probability, self._hysteresis.update(probability)
