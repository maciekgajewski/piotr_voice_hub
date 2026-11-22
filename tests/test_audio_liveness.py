"""Audio-liveness check (T-029): a stalled or all-zero stream while armed is unavailable."""

from __future__ import annotations

import asyncio
import logging

import numpy as np
import pytest
from recorded_fixtures import load_fixtures
from test_esphome_driver import Checked, Clients, arm, loud, make

from piotr_voice_hub.satellites.esphome import (
    AUDIO_LIVENESS_SECONDS,
    DIGITAL_SILENCE_RMS,
    frame_is_live,
)
from piotr_voice_hub.satellites.interfaces import MicrophoneUnavailable
from piotr_voice_hub.satellites.messages import (
    ListeningStopped,
    MicrophoneEvent,
    StartListening,
    StopListening,
)
from piotr_voice_hub.satellites.protocol import MicrophoneProtocolState
from piotr_voice_hub.satellites.vad import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE

FIXTURES = {fixture.id: fixture for fixture in load_fixtures()}


class FakeClock:
    """Test clock: `advance` wakes `sleep` waiters whose deadline has been reached."""

    def __init__(self) -> None:
        self.now = 0.0
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds
        pending: list[tuple[float, asyncio.Future[None]]] = []
        for deadline, future in self._waiters:
            if future.done():
                continue
            if deadline <= self.now:
                future.set_result(None)
            else:
                pending.append((deadline, future))
        self._waiters = pending

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._waiters.append((self.now + seconds, future))
        try:
            await future
        finally:
            self._waiters = [
                (deadline, waiter) for deadline, waiter in self._waiters if waiter is not future
            ]


async def _tick() -> None:
    await asyncio.sleep(0)


def _clip(fixture_id: str, seconds: float) -> bytes:
    pcm = FIXTURES[fixture_id].pcm()
    need = int(seconds * SAMPLE_RATE) * 2
    return (pcm * (need // len(pcm) + 1))[:need]


def zeros_pcm(seconds: float = 1.0) -> bytes:
    return _clip("silence", seconds)


def noise_pcm(seconds: float = 1.0) -> bytes:
    return _clip("noise_only", seconds)


def constant_frame(value: int) -> bytes:
    return np.full(FRAME_SAMPLES, value, dtype="<i2").tobytes()


def test_frame_is_live_rejects_digital_silence_not_a_noise_floor() -> None:
    assert "silence" in FIXTURES and "noise_only" in FIXTURES
    assert not frame_is_live(b"\0\0" * FRAME_SAMPLES)
    assert not frame_is_live(constant_frame(int(DIGITAL_SILENCE_RMS) - 1))
    assert frame_is_live(constant_frame(int(DIGITAL_SILENCE_RMS)))
    assert not frame_is_live(FIXTURES["silence"].pcm()[:FRAME_BYTES])
    assert frame_is_live(FIXTURES["noise_only"].pcm()[:FRAME_BYTES])


def _driver(clock: FakeClock | None = None) -> tuple[Checked, Clients, FakeClock]:
    clock = clock or FakeClock()
    mic, clients = make(clock=clock, sleep=clock.sleep)
    return mic, clients, clock


async def _wait_unavailable(mic: Checked) -> asyncio.Task[MicrophoneEvent]:
    task = asyncio.create_task(mic.wait_for_event())
    await _tick()
    return task


async def _assert_unavailable_once(
    mic: Checked, caplog: pytest.LogCaptureFixture, waiter: asyncio.Task[MicrophoneEvent]
) -> None:
    with pytest.raises(MicrophoneUnavailable, match="stalled stream or digital silence"):
        await waiter
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "unavailable: no live audio" in record.getMessage()
    ]
    assert len(warnings) == 1, warnings
    with pytest.raises(MicrophoneUnavailable, match="stalled stream or digital silence"):
        await mic.driver.send_output_event(StartListening("listen-2"))
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING and "unavailable: no live audio" in record.getMessage()
    ]
    assert len(warnings) == 1, warnings


def test_no_frames_for_the_bound_is_unavailable_exactly_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        caplog.set_level(logging.WARNING)
        mic, _clients, clock = _driver()
        await arm(mic)
        waiter = await _wait_unavailable(mic)
        clock.advance(AUDIO_LIVENESS_SECONDS - 0.001)
        await _tick()
        assert not waiter.done()
        clock.advance(0.001)
        await _tick()
        await _assert_unavailable_once(mic, caplog, waiter)
        await mic.close()
        mic.protocol = MicrophoneProtocolState()  # the session recreates its boundary
        await arm(mic)
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")

    asyncio.run(run())


def test_all_zero_frames_for_the_bound_are_unavailable_exactly_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        caplog.set_level(logging.WARNING)
        mic, clients, clock = _driver()
        await arm(mic)
        await clients.last.audio(zeros_pcm(1.0))
        waiter = await _wait_unavailable(mic)
        clock.advance(AUDIO_LIVENESS_SECONDS - 0.001)
        await _tick()
        assert not waiter.done()
        clock.advance(0.001)
        await _tick()
        await _assert_unavailable_once(mic, caplog, waiter)

    asyncio.run(run())


def test_quiet_nonzero_noise_floor_is_not_unavailable() -> None:
    async def run() -> None:
        mic, clients, clock = _driver()
        await arm(mic)
        deadline = clock.now + AUDIO_LIVENESS_SECONDS + 2.0
        while clock.now < deadline:
            await clients.last.audio(noise_pcm(0.32))
            clock.advance(1.0)
            await _tick()
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")

    asyncio.run(run())


def test_a_stall_that_resumes_before_the_bound_is_not_unavailable() -> None:
    async def run() -> None:
        mic, clients, clock = _driver()
        await arm(mic)
        await clients.last.audio(zeros_pcm(0.32))
        waiter = await _wait_unavailable(mic)
        clock.advance(AUDIO_LIVENESS_SECONDS - 0.1)
        await _tick()
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await clients.last.audio(loud(0.32))
        clock.advance(AUDIO_LIVENESS_SECONDS - 0.1)
        await _tick()
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")

    asyncio.run(run())


def test_nothing_triggers_when_disarmed() -> None:
    async def run() -> None:
        mic, clients, clock = _driver()
        await arm(mic)
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")
        waiter = await _wait_unavailable(mic)
        await clients.last.audio(zeros_pcm(1.0))
        clock.advance(AUDIO_LIVENESS_SECONDS + 5.0)
        await _tick()
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await arm(mic, "listen-2")

    asyncio.run(run())


def test_close_cancels_an_in_flight_liveness_watch() -> None:
    async def run() -> None:
        mic, _clients, clock = _driver()
        await arm(mic)
        watch = mic.driver._liveness_task
        assert watch is not None and not watch.done()
        await mic.close()
        await _tick()
        assert watch.done()
        assert mic.driver._liveness_task is None
        clock.advance(AUDIO_LIVENESS_SECONDS + 1.0)
        await _tick()
        mic.protocol = MicrophoneProtocolState()  # the session recreates its boundary
        await arm(mic)
        await mic.send_output_event(StopListening("listen-1", "done"))
        assert await mic.wait_for_event() == ListeningStopped("listen-1", "done")

    asyncio.run(run())
