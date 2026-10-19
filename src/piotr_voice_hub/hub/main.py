"""The hub process (T-012, design.md §4.1, §9): STT, the WebSocket API and one session per
satellite, started in that order and shut down in the reverse one."""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from piotr_voice_hub.config import HubConfig
from piotr_voice_hub.hub.watchdog import Watchdog, heartbeat
from piotr_voice_hub.tts.interfaces import TextToSpeech, TtsError, TtsUnavailableError

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
# The startup TTS check's wait for an unreachable Piper (T-016 follow-up S2): a new Piper
# container is ready after about 50 s (T-015); the old project waited 30 s for its port.
TTS_STARTUP_RETRY_SECONDS = 2.0
TTS_STARTUP_TIMEOUT_SECONDS = 120.0


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


async def check_tts(
    tts: TextToSpeech,
    *,
    retry_seconds: float = TTS_STARTUP_RETRY_SECONDS,
    timeout_seconds: float = TTS_STARTUP_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> bool:
    """The startup TTS check: Piper is reachable and describes exactly `tts.voice`. The
    service warms itself before it listens, so this is only a reachability check.

    While Piper is unreachable (`TtsUnavailableError`: refused, reset, connect timed out) it
    retries every `retry_seconds`, with a WARNING each time, for up to `timeout_seconds`:
    after a reboot or a Docker daemon restart, `depends_on` does not apply and the hub can
    start while Piper is still warming up (about 50 s). Any other `TtsError` (a wrong voice, a
    bad answer) and the overall timeout are CRITICAL, and the hub exits 1. Each attempt is
    itself bounded (the client's connect and info timeouts)."""
    started = clock()
    attempt = 0
    while True:
        attempt += 1
        try:
            await tts.start()
        except TtsUnavailableError as error:
            waited = clock() - started
            if waited + retry_seconds > timeout_seconds:
                logger.critical(
                    "TTS startup check failed, exiting: Piper not reachable within %g s "
                    "(%d attempts): %s",
                    timeout_seconds,
                    attempt,
                    error,
                )
                return False
            logger.warning(
                "waiting for Piper (attempt %d, %.0f s of %g s): %s",
                attempt,
                waited,
                timeout_seconds,
                error,
            )
            await sleep(retry_seconds)
        except TtsError as error:
            logger.critical("TTS startup check failed, exiting: %s", error)
            return False
        else:
            if attempt > 1:
                logger.info("Piper reachable after %d attempts, %.1f s", attempt, clock() - started)
            return True


async def run_hub(config: HubConfig) -> int:
    """Build the real components and run them (not unit-tested: it needs the GPU and the LAN;
    covered by the live check, T-012 log)."""
    # Imported here so --help and the unit tests do not load onnxruntime or faster-whisper.
    from piotr_voice_hub.hub.api import ApiServer
    from piotr_voice_hub.hub.playback_http import PlaybackHttpServer
    from piotr_voice_hub.hub.session import SatelliteSession
    from piotr_voice_hub.satellites.esphome import EsphomeSatellite
    from piotr_voice_hub.satellites.vad import SileroVad, load_session
    from piotr_voice_hub.stt.whisper import FasterWhisperSpeechToText
    from piotr_voice_hub.tts.wyoming_piper import WyomingPiperTts

    # First, before anything is built or armed (T-016).
    tts = WyomingPiperTts(config.tts)
    if not await check_tts(tts):
        return EXIT_FAILED
    stt = FasterWhisperSpeechToText(config.stt)
    api = ApiServer(config.api.host, config.api.port)
    # All interfaces, like the API (design §9): the satellites fetch from the host's LAN IP.
    playback = PlaybackHttpServer("0.0.0.0", config.playback_http.port)
    vad_session = load_session()
    sessions = [
        SatelliteSession(
            s,
            EsphomeSatellite(
                s, SileroVad(vad_session, s.end_silence_seconds), playback_server=playback
            ),
            stt,
            api.publish,
        )
        for s in config.satellites
    ]
    names = [s.id for s in config.satellites]
    # Started before and closed after everything else; nothing streams before the sessions.
    await playback.start()
    try:
        return await run_until_signal(stt, api, sessions, names, Watchdog())
    finally:
        await playback.close()
