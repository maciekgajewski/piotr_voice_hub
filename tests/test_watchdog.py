"""T-012 (PR #14 review B2): the event-loop watchdog."""

import asyncio
import logging
import re
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from piotr_voice_hub.hub.watchdog import Watchdog, heartbeat


def test_a_missing_heartbeat_fires_once_with_a_critical_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fired = threading.Event()
    watchdog = Watchdog(stale_seconds=0.2, check_seconds=0.02, on_stall=fired.set)
    watchdog.start()  # no heartbeat
    try:
        assert fired.wait(2.0)
    finally:
        watchdog.stop()
    (record,) = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert record.getMessage().startswith("event loop stalled: no heartbeat for ")
    assert "(limit 0.2 s); exiting" in record.getMessage()


def test_a_running_loop_keeps_it_quiet_and_stop_ends_the_thread() -> None:
    fired = threading.Event()
    watchdog = Watchdog(stale_seconds=0.2, check_seconds=0.02, on_stall=fired.set)

    async def scenario() -> None:
        watchdog.start()
        beating = asyncio.create_task(heartbeat(watchdog, beat_seconds=0.02))
        await asyncio.sleep(1.0)  # five times the limit
        beating.cancel()
        await asyncio.wait({beating})
        watchdog.stop()

    asyncio.run(scenario())
    assert not fired.is_set()
    assert not watchdog._thread.is_alive()
    time.sleep(0.3)  # stopped: no beats any more, and still nothing fires
    assert not fired.is_set()


def test_a_blocked_event_loop_exits_1_with_a_critical_line_and_a_traceback_dump() -> None:
    script = textwrap.dedent(
        """
        import asyncio, logging, time
        from piotr_voice_hub.hub.watchdog import Watchdog, heartbeat

        logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")

        async def main():
            watchdog = Watchdog(stale_seconds=0.3, check_seconds=0.05)  # the real os._exit(1)
            watchdog.start()
            asyncio.create_task(heartbeat(watchdog, beat_seconds=0.05))
            await asyncio.sleep(0.3)  # healthy for a while
            time.sleep(30)  # a blocked loop: no await, no heartbeat
            print("not reached")

        asyncio.run(main())
        """
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 1, result.stderr
    assert time.monotonic() - started < 10
    assert "not reached" not in result.stdout
    critical = result.stderr.index("CRITICAL piotr_voice_hub.hub.watchdog: event loop stalled")
    # faulthandler's dump of all threads follows: the watchdog's own, then the blocked main one
    dump = result.stderr[critical:]
    assert "Current thread 0x" in dump
    assert "(most recent call first):" in dump
    main_thread = dump.index("Thread 0x")  # the non-current one: the blocked loop
    assert re.search(r'File "<string>", line \d+ in main', dump[main_thread:]), dump


@pytest.mark.parametrize(
    "break_stderr",
    [
        "sys.stderr.close()",  # dump_traceback: ValueError
        "sys.stderr = None",  # dump_traceback: RuntimeError
        "os.close(2)",  # dump_traceback: OSError (bad file descriptor)
    ],
)
def test_an_unusable_stderr_still_exits_1(break_stderr: str) -> None:
    script = textwrap.dedent(
        f"""
        import asyncio, os, sys, time
        from piotr_voice_hub.hub.watchdog import Watchdog

        async def main():
            {break_stderr}
            Watchdog(stale_seconds=0.2, check_seconds=0.05).start()  # the real exit path
            time.sleep(30)  # a blocked loop
            print("not reached")

        asyncio.run(main())
        """
    )
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 1, result.stderr
    assert time.monotonic() - started < 10
    assert "not reached" not in result.stdout
