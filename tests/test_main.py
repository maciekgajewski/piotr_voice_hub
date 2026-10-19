"""T-012: the hub's wiring, start order and shutdown order, with fakes."""

import asyncio
import logging
import os
import signal
import time
from typing import Any

import pytest

from piotr_voice_hub.hub import main as main_module
from piotr_voice_hub.hub.main import EXIT_FAILED, EXIT_OK, check_tts, run_until_signal, serve
from piotr_voice_hub.hub.watchdog import Watchdog
from piotr_voice_hub.tts.interfaces import TtsError

TIMEOUT = 2.0


class Log:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, call: str) -> None:
        self.calls.append(call)


class FakeStt:
    def __init__(self, log: Log, error: Exception | None = None) -> None:
        self.log = log
        self.error = error

    async def start(self) -> None:
        self.log("stt.start")
        if self.error is not None:
            raise self.error


class FakeApi:
    def __init__(self, log: Log) -> None:
        self.log = log

    async def start(self) -> None:
        self.log("api.start")

    async def close(self) -> None:
        await asyncio.sleep(0)
        self.log("api.close")


class FakeSession:
    def __init__(self, name: str, log: Log, fail_after: float | None = None) -> None:
        self.name = name
        self.log = log
        self.fail_after = fail_after
        self.running = False
        self.started = asyncio.Event()

    async def run(self) -> None:
        self.log(f"{self.name}.run")
        self.running = True
        self.started.set()
        try:
            if self.fail_after is not None:
                await asyncio.sleep(self.fail_after)
                raise AssertionError(f"{self.name}: invariant broken")
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)  # run() takes a moment to unwind (abandon a segment)
            self.running = False
            self.log(f"{self.name}.run ended")

    async def shutdown(self) -> None:
        # Like SatelliteSession.shutdown(): only after run() has stopped.
        assert not self.running, f"{self.name}: shutdown() while run() is running"
        self.log(f"{self.name}.shutdown")


def build(log: Log, fail: str | None = None, stt_error: Exception | None = None):
    sessions = [
        FakeSession(name, log, fail_after=0.02 if name == fail else None) for name in ("a", "b")
    ]
    return FakeStt(log, stt_error), FakeApi(log), sessions, ["a", "b"]


def assert_ordered_shutdown(calls: list[str]) -> None:
    """Every run() ended before any shutdown(); every shutdown() before api.close()."""
    ended = [calls.index("a.run ended"), calls.index("b.run ended")]
    shutdowns = [calls.index("a.shutdown"), calls.index("b.shutdown")]
    assert max(ended) < min(shutdowns)
    assert max(shutdowns) < calls.index("api.close")
    assert calls[-1] == "api.close"


def test_start_order_then_cancel_shuts_down_in_order() -> None:
    asyncio.run(_test_start_order_then_cancel_shuts_down_in_order())


async def _test_start_order_then_cancel_shuts_down_in_order() -> None:
    log = Log()
    stt, api, sessions, names = build(log)
    task = asyncio.create_task(serve(stt, api, sessions, names))
    async with asyncio.timeout(TIMEOUT):
        for session in sessions:
            await session.started.wait()
    assert log.calls[:2] == ["stt.start", "api.start"]
    assert sorted(log.calls[2:]) == ["a.run", "b.run"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_ordered_shutdown(log.calls)


def test_a_failing_session_exits_1_after_a_full_clean_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = Log()
    stt, api, sessions, names = build(log, fail="b")
    assert asyncio.run(serve(stt, api, sessions, names)) == EXIT_FAILED
    assert_ordered_shutdown(log.calls)
    (critical,) = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical.getMessage() == "session b failed; exiting"
    assert critical.exc_info is not None and "invariant broken" in str(critical.exc_info[1])


def test_a_startup_failure_propagates_and_still_cleans_up() -> None:
    log = Log()
    stt, api, sessions, names = build(log, stt_error=RuntimeError("no CUDA device"))
    with pytest.raises(RuntimeError, match="no CUDA device"):
        asyncio.run(serve(stt, api, sessions, names))
    assert log.calls == ["stt.start", "a.shutdown", "b.shutdown", "api.close"]


def test_a_failing_shutdown_is_logged_and_the_api_still_closes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = Log()
    stt, api, sessions, names = build(log)

    async def broken() -> None:
        raise OSError("satellite gone")

    sessions[0].shutdown = broken  # type: ignore[method-assign]

    async def scenario() -> None:
        task = asyncio.create_task(serve(stt, api, sessions, names))
        await sessions[1].started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert log.calls[-2:] == ["b.shutdown", "api.close"]
    assert "session a: shutdown failed: OSError('satellite gone')" in caplog.text


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_shuts_down_in_order_and_exits_0(signum: int) -> None:
    log = Log()
    stt, api, sessions, names = build(log)

    async def scenario() -> int:
        task = asyncio.create_task(run_until_signal(stt, api, sessions, names))
        async with asyncio.timeout(TIMEOUT):
            for session in sessions:
                await session.started.wait()
        os.kill(os.getpid(), signum)
        async with asyncio.timeout(TIMEOUT):
            return await task

    assert asyncio.run(scenario()) == EXIT_OK
    assert_ordered_shutdown(log.calls)
    # The handlers are removed again: the default SIGINT handler is back.
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_a_session_failure_under_run_until_signal_exits_1() -> None:
    log = Log()
    stt, api, sessions, names = build(log, fail="a")
    assert asyncio.run(run_until_signal(stt, api, sessions, names)) == EXIT_FAILED
    assert_ordered_shutdown(log.calls)


def test_signal_handlers_are_removed_when_cleanup_starts() -> None:
    log = Log()
    stt, api, sessions, names = build(log)
    seen: list[object] = []

    async def shutdown() -> None:
        seen.append(signal.getsignal(signal.SIGTERM))
        log("a.shutdown")

    sessions[0].shutdown = shutdown  # type: ignore[method-assign]

    async def scenario() -> int:
        task = asyncio.create_task(run_until_signal(stt, api, sessions, names))
        await sessions[1].started.wait()
        os.kill(os.getpid(), signal.SIGTERM)
        return await task

    assert asyncio.run(scenario()) == EXIT_OK
    assert seen == [signal.SIG_DFL]  # a second SIGTERM during cleanup ends the process


class SlowToStopSession(FakeSession):
    """run() keeps going after it is cancelled, until the test releases it."""

    def __init__(self, name: str, log: Log) -> None:
        super().__init__(name, log)
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.log(f"{self.name}.run")
        self.running = True
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.release.wait()
            raise
        finally:
            self.running = False


def test_a_run_that_does_not_stop_in_time_is_not_shut_down(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(main_module, "RUN_CANCEL_TIMEOUT_SECONDS", 0.05)
    log = Log()
    slow = SlowToStopSession("a", log)
    sessions = [slow, FakeSession("b", log)]

    async def scenario() -> None:
        task = asyncio.create_task(serve(FakeStt(log), FakeApi(log), sessions, ["a", "b"]))
        await slow.started.wait()
        await sessions[1].started.wait()
        task.cancel()
        async with asyncio.timeout(TIMEOUT):
            with pytest.raises(asyncio.CancelledError):
                await task
        slow.release.set()  # let it end so asyncio.run can finish
        await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert "session a did not stop within 0.05 s" in caplog.text
    assert "a.shutdown" not in log.calls
    assert log.calls[-2:] == ["b.shutdown", "api.close"]


def test_the_watchdog_runs_with_the_hub_and_stops_after_cleanup() -> None:
    log = Log()
    stt, api, sessions, names = build(log)
    fired: list[bool] = []
    watchdog = Watchdog(stale_seconds=5.0, check_seconds=0.01, on_stall=lambda: fired.append(True))
    beats: list[float] = []
    original = watchdog.beat

    def beat() -> None:
        beats.append(time.monotonic())
        original()

    watchdog.beat = beat  # type: ignore[method-assign]

    async def scenario() -> int:
        task = asyncio.create_task(run_until_signal(stt, api, sessions, names, watchdog))
        await sessions[1].started.wait()
        assert watchdog._thread.is_alive()
        os.kill(os.getpid(), signal.SIGTERM)
        return await task

    assert asyncio.run(scenario()) == EXIT_OK
    assert beats and not fired
    assert not watchdog._thread.is_alive()
    assert_ordered_shutdown(log.calls)


class FakeTts:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.started = 0

    async def start(self) -> None:
        self.started += 1
        if self.error is not None:
            raise self.error

    def synthesize(self, text: str) -> Any:
        raise AssertionError("not used at startup")


def test_the_tts_check_passes_when_piper_describes_the_voice() -> None:
    tts = FakeTts()
    assert asyncio.run(check_tts(tts)) is True
    assert tts.started == 1


def test_a_wrong_voice_fails_the_tts_check_with_a_critical_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = TtsError(
        "tts.voice is 'pl_PL-gosia-medium', but Piper at 127.0.0.1:10201 "
        "describes ['pl_PL-bass-high']"
    )
    assert asyncio.run(check_tts(FakeTts(error))) is False
    record = caplog.records[-1]
    assert record.levelno == logging.CRITICAL
    assert "TTS startup check failed, exiting" in record.getMessage()
    assert "pl_PL-gosia-medium" in record.getMessage()
    assert "['pl_PL-bass-high']" in record.getMessage()


def test_other_errors_in_the_tts_check_propagate() -> None:
    with pytest.raises(LookupError):
        asyncio.run(check_tts(FakeTts(LookupError())))
