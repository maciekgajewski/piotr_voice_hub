import hashlib
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from piotr_voice_hub.satellites.vad import (
    FRAME_BYTES,
    FRAME_SAMPLES,
    MODEL_PATH,
    SAMPLE_RATE,
    START_THRESHOLD,
    Hysteresis,
    SileroVad,
    VadTransition,
    load_session,
)

MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
SPEECH_CLIP = Path(__file__).parent / "data" / "speech_pl_16k.wav"
START_FRAMES = 8  # ceil(250 ms / 32 ms)
END_FRAMES = 25  # 0.8 s / 32 ms

STARTED = VadTransition.SPEECH_STARTED
ENDED = VadTransition.SPEECH_ENDED


def feed(hysteresis: Hysteresis, probabilities: list[float]) -> list[tuple[int, VadTransition]]:
    """Return (frame index, transition) for every transition."""
    events = []
    for index, probability in enumerate(probabilities):
        transition = hysteresis.update(probability)
        if transition is not None:
            events.append((index, transition))
    return events


def test_model_is_the_pinned_silero_release() -> None:
    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == MODEL_SHA256
    assert MODEL_PATH.with_name("silero_vad.LICENSE").read_text().startswith("MIT License")


def test_hub_environment_never_gets_onnxruntime_gpu() -> None:
    """design.md §9: the hub uses CPU onnxruntime and must never also install onnxruntime-gpu."""
    lock = (Path(__file__).resolve().parent.parent / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "onnxruntime"' in lock
    assert 'name = "onnxruntime-gpu"' not in lock


def test_starts_after_250_ms_at_or_above_threshold() -> None:
    events = feed(Hysteresis(0.8), [0.5] * START_FRAMES)
    assert events == [(START_FRAMES - 1, STARTED)]


def test_short_burst_does_not_start() -> None:
    probabilities = [0.9] * (START_FRAMES - 1) + [0.49] + [0.9] * (START_FRAMES - 1)
    assert feed(Hysteresis(0.8), probabilities) == []


def test_ends_after_end_silence_below_threshold() -> None:
    probabilities = [0.9] * START_FRAMES + [0.34] * END_FRAMES
    assert feed(Hysteresis(0.8), probabilities) == [
        (START_FRAMES - 1, STARTED),
        (START_FRAMES + END_FRAMES - 1, ENDED),
    ]


def test_probability_at_end_threshold_resets_silence() -> None:
    probabilities = (
        [0.9] * START_FRAMES + [0.1] * (END_FRAMES - 1) + [0.35] + [0.1] * (END_FRAMES - 1)
    )
    hysteresis = Hysteresis(0.8)
    assert feed(hysteresis, probabilities) == [(START_FRAMES - 1, STARTED)]
    assert hysteresis.speaking
    assert hysteresis.update(0.1) is ENDED


def test_mid_probabilities_keep_speech_going() -> None:
    hysteresis = Hysteresis(0.8)
    assert feed(hysteresis, [0.9] * START_FRAMES + [0.4] * 100) == [(START_FRAMES - 1, STARTED)]
    assert hysteresis.speaking


def test_restart_needs_a_full_start_run_again() -> None:
    probabilities = [0.9] * START_FRAMES + [0.0] * END_FRAMES + [0.9] * START_FRAMES
    events = feed(Hysteresis(0.8), probabilities)
    assert [t for _, t in events] == [STARTED, ENDED, STARTED]
    assert events[2][0] == 2 * START_FRAMES + END_FRAMES - 1


def test_end_silence_comes_from_config() -> None:
    events = feed(Hysteresis(0.32), [0.9] * START_FRAMES + [0.0] * 10)
    assert events[1] == (START_FRAMES + 10 - 1, ENDED)


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan")])
def test_rejects_invalid_probability(probability: float) -> None:
    with pytest.raises(AssertionError):
        Hysteresis(0.8).update(probability)


def test_rejects_wrong_frame_size() -> None:
    vad = SileroVad(load_session(), 0.8)
    with pytest.raises(AssertionError, match="VAD frame must be"):
        vad.process_frame(b"\0" * (FRAME_BYTES - 2))


def run_model(pcm: bytes) -> tuple[list[float], list[tuple[int, VadTransition]]]:
    vad = SileroVad(load_session(), 0.8)
    probabilities, events = [], []
    for index in range(len(pcm) // FRAME_BYTES):
        probability, transition = vad.process_frame(
            pcm[index * FRAME_BYTES : (index + 1) * FRAME_BYTES]
        )
        probabilities.append(probability)
        if transition is not None:
            events.append((index, transition))
    return probabilities, events


def silence(seconds: float) -> bytes:
    return b"\0\0" * int(seconds * SAMPLE_RATE)


def speech_clip() -> bytes:
    with wave.open(str(SPEECH_CLIP), "rb") as clip:
        assert (clip.getframerate(), clip.getsampwidth(), clip.getnchannels()) == (
            SAMPLE_RATE,
            2,
            1,
        )
        return clip.readframes(clip.getnframes())


def test_model_stays_quiet_on_silence_and_noise() -> None:
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(SAMPLE_RATE * 2) * 300).astype("<i2").tobytes()
    probabilities, events = run_model(silence(2.0) + noise)
    assert events == []
    assert max(probabilities) < 0.35


def test_model_segments_real_speech() -> None:
    """Silence, a 2.1 s Polish clip, silence: exactly one segment, ending after the clip."""
    speech = speech_clip()
    pcm = silence(1.0) + speech + silence(1.5)
    _, events = run_model(pcm)
    assert [t for _, t in events] == [STARTED, ENDED]
    start_s, end_s = (index * FRAME_SAMPLES / SAMPLE_RATE for index, _ in events)
    speech_end_s = 1.0 + len(speech) / 2 / SAMPLE_RATE
    assert 1.0 <= start_s <= 1.6
    assert speech_end_s + 0.5 <= end_s <= speech_end_s + 1.5


def test_frame_cost_is_small() -> None:
    """Loose guard only (shared CI runners); the measured per-frame time is in log.md."""
    vad = SileroVad(load_session(), 0.8)
    frame = speech_clip()[:FRAME_BYTES]
    vad.process_frame(frame)
    start = time.perf_counter()
    for _ in range(100):
        vad.process_frame(frame)
    assert (time.perf_counter() - start) / 100 < 0.016  # under half a frame


ALARM_CLIP = Path(__file__).parent / "data" / "alarm_tone_16k.wav"


def test_reset_ends_the_segment_and_a_new_one_needs_a_full_start_run() -> None:
    hysteresis = Hysteresis(0.8)
    assert feed(hysteresis, [0.9] * START_FRAMES) == [(START_FRAMES - 1, STARTED)]
    hysteresis.reset()  # the 15 s max-segment cut, while speech goes on
    assert not hysteresis.speaking
    assert feed(hysteresis, [0.9] * (START_FRAMES - 1)) == []
    assert feed(hysteresis, [0.9]) == [(0, STARTED)]


def test_reset_forgets_a_partial_start_run_and_the_silence_run() -> None:
    hysteresis = Hysteresis(0.8)
    feed(hysteresis, [0.9] * (START_FRAMES - 1))
    hysteresis.reset()
    assert feed(hysteresis, [0.9] * (START_FRAMES - 1)) == []  # the old run doesn't count
    hysteresis.reset()
    feed(hysteresis, [0.9] * START_FRAMES + [0.0] * (END_FRAMES - 1))
    hysteresis.reset()
    assert not hysteresis.speaking
    assert feed(hysteresis, [0.0] * END_FRAMES) == []  # no ENDED without a segment


def test_silero_reset_and_force_end() -> None:
    vad = SileroVad(load_session(), 0.8)
    speech = speech_clip()
    transitions = []
    for index in range(len(speech) // FRAME_BYTES):
        _, transition = vad.process_frame(speech[index * FRAME_BYTES : (index + 1) * FRAME_BYTES])
        transitions.append(transition)
    assert STARTED in transitions and vad.speaking
    vad.force_end()
    assert not vad.speaking
    vad.reset()
    assert not vad.speaking
    assert run_model(silence(1.0) + speech)[1][0][1] is STARTED  # still works after a reset


def test_model_does_not_open_a_segment_on_a_loud_alarm_tone() -> None:
    """A full-scale alarm tone (peak 32758) tripped the old peak gate; Silero ignores it."""
    with wave.open(str(ALARM_CLIP), "rb") as clip:
        assert clip.getframerate() == SAMPLE_RATE
        assert (clip.getsampwidth(), clip.getnchannels()) == (2, 1)
        alarm = clip.readframes(clip.getnframes())
    assert np.abs(np.frombuffer(alarm, "<i2")).max() > 30000
    probabilities, events = run_model(alarm + alarm + silence(1.0))
    assert events == []
    assert max(probabilities) < START_THRESHOLD


def test_vad_docstring_explains_the_pre_roll() -> None:
    import piotr_voice_hub.satellites.vad as vad_module

    assert vad_module.__doc__ is not None and "pre-roll" in vad_module.__doc__
