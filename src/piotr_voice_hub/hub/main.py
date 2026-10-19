"""The hub process (T-012, design.md §4.1, §9): STT, the WebSocket API and one session per
satellite, started in that order and shut down in the reverse one."""

import asyncio
import logging
import signal
from collections.abc import Callable, Sequence
from typing import Protocol

from piotr_voice_hub.config import HubConfig
from piotr_voice_hub.hub.watchdog import Watchdog, heartbeat

EXIT_OK = 0
EXIT_FAILED = 1  # a session or startup failed: Docker restarts the container (§9)
# Other exit codes: 2 invalid config (cli), 70 STT fatal (stt.whisper.exit_process); a stalled
# event loop exits 1 too (hub.watchdog).
# A cancelled run() unwinds (abandons a segment) well within this; one still running after it is
# logged and not shut down, so a stuck session cannot hold up the exit.
RUN_CANCEL_TIMEOUT_SECONDS = 10.0

# Library loggers that are too chatty at INFO for a service log.
QUIET_LOGGERS = {
    "aioesphomeapi": logging.ERROR,  # its own WARNING per failed connect repeats the session's
    "piotr_voice_hub.hub.api.websockets": logging.WARNING,  # one INFO line per /healthz
    "faster_whisper": logging.WARNING,  # one INFO line per model call, i.e. every partial
}

logger = logging.getLogger(__name__)


class Stt(Protocol):
    async def start(self) -> None: ...


class Api(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...


class Session(Protocol):
    async def run(self) -> None: ...
    async def shutdown(self) -> None: ...


def quiet_library_loggers() -> None:
    for name, level in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


async def serve(
    stt: Stt,
    api: Api,
    sessions: Sequence[Session],
    names: Sequence[str],
    before_cleanup: Callable[[], None] = lambda: None,
) -> int:
    """Run until cancelled (re-raises CancelledError) or until a session fails: that failure
    is logged CRITICAL with its traceback at once, then the clean shutdown below runs, then
    this returns EXIT_FAILED.

    Shutdown order: `before_cleanup()`, cancel every `run()` and wait for it (bounded), then
    `shutdown()` every session whose `run()` has stopped, then the API. The STT has no close
    (T-008)."""
    tasks: list[asyncio.Task[None]] = []
    try:
        await stt.start()
        await api.start()
        tasks = [
            asyncio.create_task(session.run(), name=f"session {name}")
            for session, name in zip(sessions, names, strict=True)
        ]
        logger.info("hub running: %d satellite(s) (%s)", len(sessions), ", ".join(names))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:  # run() never returns normally: it ends only by raising
            error = task.exception()
            logger.critical("%s failed; exiting", task.get_name(), exc_info=error)
        return EXIT_FAILED
    finally:
        before_cleanup()
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=RUN_CANCEL_TIMEOUT_SECONDS)
            for task in pending:
                logger.error(
                    "%s did not stop within %g s", task.get_name(), RUN_CANCEL_TIMEOUT_SECONDS
                )
        stopped = [
            (session, name)
            for i, (session, name) in enumerate(zip(sessions, names, strict=True))
            if not tasks or tasks[i].done()
        ]
        results = await asyncio.gather(
            *(session.shutdown() for session, _ in stopped), return_exceptions=True
        )
        for (_, name), result in zip(stopped, results, strict=True):
            if isinstance(result, BaseException):
                logger.error("session %s: shutdown failed: %r", name, result)
        await api.close()
        logger.info("hub stopped")


async def run_until_signal(
    stt: Stt,
    api: Api,
    sessions: Sequence[Session],
    names: Sequence[str],
    watchdog: Watchdog | None = None,
) -> int:
    """`serve`, with SIGTERM (docker stop) and SIGINT cancelling it (exit 0 after a signal),
    and the event-loop watchdog. The handlers are removed when cleanup starts, so a second
    signal during cleanup has its default effect (the process ends at once). The heartbeat
    keeps beating through cleanup, which is bounded (well under STALE_SECONDS), and the
    watchdog is stopped after it, so it fires during shutdown only if the loop really hangs
    there; exiting 1 then is acceptable (T-012 plan)."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    signals = (signal.SIGTERM, signal.SIGINT)

    def remove_signal_handlers() -> None:
        for signum in signals:
            loop.remove_signal_handler(signum)

    for signum in signals:
        loop.add_signal_handler(signum, task.cancel)
    beating: asyncio.Task[None] | None = None
    if watchdog is not None:
        watchdog.start()
        beating = asyncio.create_task(heartbeat(watchdog), name="heartbeat")
    try:
        return await serve(stt, api, sessions, names, remove_signal_handlers)
    except asyncio.CancelledError:
        logger.info("stopped by signal")
        return EXIT_OK
    finally:
        remove_signal_handlers()  # again, in case serve() never got to its cleanup
        if beating is not None:
            beating.cancel()
            await asyncio.wait({beating})
        if watchdog is not None:
            watchdog.stop()


async def run_hub(config: HubConfig) -> int:
    """Build the real components and run them (not unit-tested: it needs the GPU and the LAN;
    covered by the live check, T-012 log)."""
    # Imported here so --help and the unit tests do not load onnxruntime or faster-whisper.
    from piotr_voice_hub.hub.api import ApiServer
    from piotr_voice_hub.hub.session import SatelliteSession
    from piotr_voice_hub.satellites.esphome import EsphomeSatellite
    from piotr_voice_hub.satellites.vad import SileroVad, load_session
    from piotr_voice_hub.stt.whisper import FasterWhisperSpeechToText

    stt = FasterWhisperSpeechToText(config.stt)
    api = ApiServer(config.api.host, config.api.port)
    vad_session = load_session()
    sessions = [
        SatelliteSession(
            s, EsphomeSatellite(s, SileroVad(vad_session, s.end_silence_seconds)), stt, api.publish
        )
        for s in config.satellites
    ]
    names = [s.id for s in config.satellites]
    return await run_until_signal(stt, api, sessions, names, Watchdog())
