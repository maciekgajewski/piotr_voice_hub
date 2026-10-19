"""T-012: the hub's wiring, start order and shutdown order, with fakes."""

import asyncio
import logging
import os
import signal

import pytest

from piotr_voice_hub.hub.main import EXIT_FAILED, EXIT_OK, run_until_signal, serve

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
