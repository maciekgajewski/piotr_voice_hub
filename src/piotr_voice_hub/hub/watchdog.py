"""Event-loop watchdog (T-012, PR #14 review B2).

Docker's healthcheck only marks a container unhealthy; it never restarts it. Restarts come from
the process exiting (`restart: unless-stopped`). So a hung event loop must end the process: a
heartbeat task on the loop stamps the time every `beat_seconds`, and a plain thread (not a
task: it must run while the loop is blocked) exits the process with code 1 when the stamp is
older than `stale_seconds`, after logging CRITICAL and dumping every thread's stack.
"""

import asyncio
import faulthandler
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import NoReturn

BEAT_SECONDS = 1.0
STALE_SECONDS = 30.0
CHECK_SECONDS = 1.0
EXIT_STALLED = 1

logger = logging.getLogger(__name__)


def exit_process() -> NoReturn:
    # Where every thread is stuck, for the post-mortem (`docker compose logs`). faulthandler
    # writes straight to the file descriptor, with no logging or locks involved.
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    os._exit(EXIT_STALLED)  # not sys.exit: the loop is wedged and cannot unwind


class Watchdog:
    """`start()` with the loop running, `beat()` from the loop (see `heartbeat`), `stop()`."""

    def __init__(
        self,
        stale_seconds: float = STALE_SECONDS,
        check_seconds: float = CHECK_SECONDS,
        on_stall: Callable[[], None] = exit_process,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stale_seconds = stale_seconds
        self._check_seconds = check_seconds
        self._on_stall = on_stall
        self._clock = clock
        self._last_beat = clock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="loop-watchdog", daemon=True)

    def beat(self) -> None:
        self._last_beat = self._clock()  # a float store: atomic under the GIL

    def start(self) -> None:
        self.beat()
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self._check_seconds + 1.0)

    def _watch(self) -> None:
        while not self._stopped.wait(self._check_seconds):
            stale = self._clock() - self._last_beat
            if stale > self._stale_seconds:
                logger.critical(
                    "event loop stalled: no heartbeat for %.1f s (limit %g s); exiting",
                    stale,
                    self._stale_seconds,
                )
                self._on_stall()
                return


async def heartbeat(watchdog: Watchdog, beat_seconds: float = BEAT_SECONDS) -> None:
    """Runs on the event loop until cancelled."""
    while True:
        watchdog.beat()
        await asyncio.sleep(beat_seconds)
