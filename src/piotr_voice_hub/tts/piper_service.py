"""Startup of the hub's own wyoming-piper service (T-015, design.md §9).

Runs inside the Piper image (docker/piper.Dockerfile), not in the hub: the image copies this
file next to docker/piper/entrypoint.py, which supplies the real piper and wyoming-piper. It
imports only the stdlib and onnxruntime, so the hub's tests cover it on the CPU onnxruntime.

Before wyoming-piper serves anything, `main()`:
1. checks that the only onnxruntime installed is onnxruntime-gpu 1.26.0 (also at build time:
   1.28+ are CUDA 13 builds, and the CPU `onnxruntime` next to it makes it run on CPU), and that
   the imported module is that 1.26.0 GPU build;
2. creates the voice's ONNX session itself, asking for the CUDA EP only, with cuDNN's
   HEURISTIC conv algorithm search, and fails unless CUDA is the session's first provider: when
   the CUDA EP cannot load, onnxruntime silently runs the whole model on CPU. (ORT's
   `session.disable_cpu_ep_fallback` cannot be used: this voice has 678 shape ops that ORT
   always places on CPU, next to 2036 nodes on CUDA, so the session would not start; T-015 log);
3. warms the voice up, each sentence bounded (the first inference is a one-time CUDA compile,
   about 48 s per new container, T-015), so the port opens, and the healthcheck turns green,
   only when warm; then re-times the long sentence and fails if it is not fast (CPU placement);
4. hands the voice to wyoming-piper's one-voice cache, wrapped in `GuardedVoice` (a text cap,
   a watchdog per synthesis, exit on an onnxruntime run-time error), and makes any other voice
   fail before it is downloaded or loaded (the voice is baked into the image, pinned), and
   makes Describe list only that voice (so a client asking for another one fails its check);
5. runs wyoming-piper with `--use-cuda` and without `--update-voices`.

Any failure at startup raises, so the process exits 1 and Docker restarts it. While serving,
wyoming-piper synthesizes on its single event-loop thread with no bound of its own, so a hang
or a lasting CUDA error exits the process (os._exit) for the same restart: Docker never
restarts a merely unhealthy container.
"""

import argparse
import faulthandler
import importlib.metadata
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

import onnxruntime
from onnxruntime.capi.onnxruntime_pybind11_state import EngineError, EPFail, Fail, RuntimeException

CUDA = "CUDAExecutionProvider"
ORT_GPU = "onnxruntime-gpu"
ORT_GPU_VERSION = "1.26.0"
ORT_CPU = "onnxruntime"
CUDA_PROVIDER = (CUDA, {"cudnn_conv_algo_search": "HEURISTIC"})
SAMPLES_PER_CHUNK = 1024  # as the old project ran it: 46 ms at 22050 Hz
# A short, a long (about 200 characters) and a near-cap sentence (950 characters, one
# sentence, so one ONNX call), so the first real request of any length is not the first
# inference: CUDA loads kernels lazily, so a much longer input can still need one-time work.
WARM_CHECK_TEXT = (
    "Jutro w Warszawie będzie pochmurno, rano około dwunastu stopni, a po południu przelotny "
    "deszcz i umiarkowany wiatr z zachodu, więc przed wyjściem z domu warto zabrać parasol i "
    "cieplejszą kurtkę."
)
NEAR_CAP_TEXT = (
    "Kiedy wieczorem wrócisz do domu po długim dniu w pracy, zostawisz buty w przedpokoju, "
    "odwiesisz mokrą kurtkę na wieszak przy drzwiach i zapalisz światło w kuchni, przypomnę ci,"
    " że w lodówce czeka jeszcze wczorajsza zupa pomidorowa, że na balkonie trzeba podlać "
    "pomidory i bazylię, które od trzech dni nie widziały wody, że w łazience kończy się pasta "
    "do zębów, a w szafce pod zlewem brakuje płynu do naczyń, że jutro rano o wpół do ósmej "
    "przyjeżdża kurier z paczką od cioci Basi z Krakowa, więc ktoś musi być w domu albo "
    "zostawić kartkę na drzwiach, że w sobotę są urodziny Kasi, na które obiecałeś upiec sernik"
    " z rodzynkami według przepisu babci, że pralka skończyła już pranie i ręczniki trzeba "
    "rozwiesić, zanim zaczną pachnieć, że rachunek za prąd trzeba zapłacić do piątku, a "
    "samochód zawieźć do mechanika na wymianę opon na zimowe, i że prognoza na jutro mówi o "
    "porannym przymrozku, gęstej mgle nad rzeką i silnym wietrze przez całe popołudnie."
)
WARMUP_TEXTS = ("Dzień dobry, tu Ryszard.", WARM_CHECK_TEXT, NEAR_CAP_TEXT)
# Per warm-up sentence: the first inference in a new container is a one-time CUDA compile
# for sm_120, about 48 s (T-003, T-015).
WARMUP_TIMEOUT_SECONDS = 120.0
# Re-timed once warm: the ~200-character sentence took 0.11-0.18 s on CUDA (T-015); on CPU it
# would be several times slower, so over this the voice is not really on the GPU. 2 s leaves
# room for Ollama time-slicing the GPU at startup (#18 review N3).
WARM_LIMIT_SECONDS = 2.0
# Per synthesis call while serving (wyoming-piper calls the voice once per sentence). Longer
# text is refused with a Wyoming `error`: one huge "sentence" would block the service and take
# VRAM next to Ollama. The hub's client (T-016) rejects a whole text over this many
# characters before sending it, so this is the second guard.
MAX_TEXT_CHARS = 1000
# Per synthesis call while serving: 191 characters take 0.18 s warm (T-015), so a call of
# MAX_TEXT_CHARS takes about 1 s. A call still running after this is a wedged CUDA call, which
# cannot be cancelled: faulthandler dumps every thread's stack and exits the process, and
# Docker restarts it.
SYNTHESIS_TIMEOUT_SECONDS = 30.0
# onnxruntime's run-time failures. A CUDA error (an illegal address after an OOM, say) is
# sticky: every later run fails too, so the process exits and Docker restarts it. Errors about
# the input (InvalidArgument, ...) are not in this list; they go back to the client.
ORT_RUNTIME_ERRORS = (Fail, RuntimeException, EPFail, EngineError)
# wyoming-piper 2.5.2 internals this module relies on (checked at startup).
HANDLER_ATTRIBUTES = ("_VOICE", "_VOICE_NAME", "PiperVoice", "ensure_voice_exists")
# wyoming_piper.__main__: builds the Describe answer (Info) from the bundled catalog.
SERVER_INFO_ATTRIBUTE = "_piper_info"
WYOMING_PIPER_ARGS_ENV = "WYOMING_PIPER_ARGS"

logger = logging.getLogger("piper_service")


class InstallError(RuntimeError):
    """The image does not have exactly onnxruntime-gpu 1.26.0."""


def check_install(
    version_of: Callable[[str], str] = importlib.metadata.version, ort: Any = onnxruntime
) -> None:
    """Fail unless onnxruntime-gpu is 1.26.0, the CPU-only onnxruntime is absent and the
    imported module (`ort`) is that build: another dist can provide the same package."""
    try:
        cpu = version_of(ORT_CPU)
    except importlib.metadata.PackageNotFoundError:
        pass
    else:
        raise InstallError(f"{ORT_CPU} {cpu} is installed next to {ORT_GPU}: it runs on CPU")
    try:
        gpu = version_of(ORT_GPU)
    except importlib.metadata.PackageNotFoundError:
        raise InstallError(f"{ORT_GPU} is not installed") from None
    if gpu != ORT_GPU_VERSION:
        raise InstallError(f"{ORT_GPU} is {gpu}, need {ORT_GPU_VERSION} (the CUDA 12 build)")
    if ort.__version__ != ORT_GPU_VERSION or ort.get_device() != "GPU":
        raise InstallError(
            f"the imported onnxruntime is {ort.__version__} {ort.get_device()}, "
            f"need {ORT_GPU_VERSION} GPU"
        )


def create_session(
    model: Path,
    available: Callable[[], Sequence[str]] = onnxruntime.get_available_providers,
    factory: Callable[..., Any] = onnxruntime.InferenceSession,
) -> Any:
    """The voice's session on the CUDA EP; raises unless CUDA is really used."""
    if CUDA not in available():
        raise RuntimeError(f"{CUDA} is not available (providers: {list(available())})")
    options = onnxruntime.SessionOptions()
    session = factory(str(model), sess_options=options, providers=[CUDA_PROVIDER])
    providers = list(session.get_providers())
    if not providers or providers[0] != CUDA:
        raise RuntimeError(f"voice session is not on {CUDA} (providers: {providers})")
    logger.info("voice session providers: %s", providers)
    return session


def _arm_watchdog(timeout: float) -> None:
    """faulthandler's watchdog: a C thread that needs no GIL, so it fires even when the
    synthesis holds the GIL (a hang in piper's espeak bridge, say); it writes every thread's
    stack to stderr (the container log) and calls _exit(1). It has one slot per process,
    which fits: wyoming-piper runs one synthesis at a time (`_VOICE_LOCK`, on its event-loop
    thread), and nothing else in this process uses it."""
    faulthandler.dump_traceback_later(timeout, exit=True)


def _exit_now(code: int) -> NoReturn:
    os._exit(code)  # a wedged CUDA call cannot be cancelled; sys.exit would wait for it


def _timed_synthesis(
    synthesize: Callable[[str], float],
    text: str,
    timeout: float,
    clock: Callable[[], float],
    exit_now: Callable[[int], NoReturn],
    label: str,
) -> float:
    """Synthesize `text` once; returns the seconds it took. An error raises; a call that runs
    past `timeout` exits the process at once. `label` names the step in the logs."""
    result: dict[str, float | BaseException] = {}

    def run() -> None:
        try:
            result["audio"] = synthesize(text)
        except BaseException as error:  # re-raised in the caller's thread below
            result["error"] = error

    start = clock()
    thread = threading.Thread(target=run, name=f"piper-{label.replace(' ', '-')}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        logger.critical("%s synthesis did not finish within %g s", label, timeout)
        exit_now(1)
    elapsed = clock() - start
    failure = result.get("error")
    if isinstance(failure, BaseException):
        raise RuntimeError(f"{label} synthesis failed: {failure!r}") from failure
    audio = result["audio"]
    assert isinstance(audio, float)
    if audio <= 0:
        raise RuntimeError(f"{label} synthesis gave no audio for {text[:40]!r}...")
    logger.info("%s: %d characters, %.2f s of audio in %.2f s", label, len(text), audio, elapsed)
    return elapsed


def warm_up(
    synthesize: Callable[[str], float],
    texts: Sequence[str] = WARMUP_TEXTS,
    timeout: float = WARMUP_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    exit_now: Callable[[int], NoReturn] = _exit_now,
) -> list[float]:
    """Synthesize each text once; returns the seconds each took. An error raises; a call that
    runs past `timeout` exits the process at once."""
    return [
        _timed_synthesis(synthesize, text, timeout, clock, exit_now, "warm-up") for text in texts
    ]


def check_warm(
    synthesize: Callable[[str], float],
    text: str = WARM_CHECK_TEXT,
    limit: float = WARM_LIMIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    exit_now: Callable[[int], NoReturn] = _exit_now,
) -> float:
    """Re-time the ~200-character warm-up sentence once warm; raises if it took over `limit`
    (a run-time guard against the model running on CPU after all)."""
    elapsed = _timed_synthesis(
        synthesize, text, WARMUP_TIMEOUT_SECONDS, clock, exit_now, "warm check"
    )
    if elapsed > limit:
        raise RuntimeError(
            f"warm synthesis of {len(text)} characters took {elapsed:.2f} s, over {limit:g} s: "
            "the voice is not running on the GPU"
        )
    logger.info("warm check passed: %.2f s (limit %g s)", elapsed, limit)
    return elapsed


class TextTooLongError(ValueError):
    """A synthesis call over MAX_TEXT_CHARS; wyoming-piper sends it to the client as `error`."""


class GuardedVoice:
    """Stands in for the warmed `PiperVoice` in wyoming-piper's cache while serving.

    wyoming-piper 2.5.2 calls `synthesize_wav(text, wav_writer, syn_config)` on its event-loop
    thread, once per sentence, with no bound. Each call here: refuses text over `max_chars`
    (an exception, which wyoming-piper turns into an `error` event); arms a watchdog
    (`_arm_watchdog`) that dumps every thread's stack and exits the process if the call runs
    past `timeout`; and exits the process on an onnxruntime run-time error. wyoming-piper also
    reads `.config` (speaker ids)."""

    def __init__(
        self,
        voice: Any,
        max_chars: int = MAX_TEXT_CHARS,
        timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
        exit_now: Callable[[int], NoReturn] = _exit_now,
        arm: Callable[[float], None] = _arm_watchdog,
        disarm: Callable[[], None] = faulthandler.cancel_dump_traceback_later,
    ) -> None:
        self.voice = voice
        self._max_chars = max_chars
        self._timeout = timeout
        self._exit_now = exit_now
        self._arm = arm
        self._disarm = disarm

    @property
    def config(self) -> Any:
        return self.voice.config

    def synthesize_wav(self, text: str, wav_file: Any, syn_config: Any = None) -> None:
        if len(text) > self._max_chars:
            logger.warning("refused a synthesis of %d characters", len(text))
            raise TextTooLongError(
                f"text is {len(text)} characters, the limit per call is {self._max_chars}"
            )
        self._arm(self._timeout)
        try:
            self.voice.synthesize_wav(text, wav_file, syn_config)
        except ORT_RUNTIME_ERRORS as error:
            logger.critical("onnxruntime failed during a synthesis, exiting: %r", error)
            self._exit_now(1)
        finally:
            self._disarm()


class OnlyThePreloadedVoice:
    """Stands in for wyoming-piper's `PiperVoice`: the service serves one voice, created and
    checked by `create_session`, and never loads (or downloads) another."""

    @staticmethod
    def load(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError(f"only the preloaded voice is served (asked to load {args[:1]})")


def refuse_download(name: str, *args: object, **kwargs: object) -> NoReturn:
    """Stands in for wyoming-piper's `ensure_voice_exists`, which it calls (and which may
    download) before loading a voice other than the cached one."""
    raise RuntimeError(f"only the preloaded voice is served (asked for {name!r})")


def install_voice(handler: ModuleType, voice: object, name: str) -> None:
    """Put the warmed voice, wrapped in `GuardedVoice`, into wyoming-piper's one-voice cache;
    any other voice fails before wyoming-piper downloads or loads it. Describe lists only this
    voice too (`advertise_only`)."""
    missing = [attr for attr in HANDLER_ATTRIBUTES if not hasattr(handler, attr)]
    if missing:
        raise RuntimeError(
            f"{handler.__name__} has no {missing}: not the pinned wyoming-piper 2.5.2"
        )
    setattr(handler, "_VOICE", GuardedVoice(voice))  # noqa: B010 (globals of the pinned version)
    setattr(handler, "_VOICE_NAME", name)  # noqa: B010
    setattr(handler, "PiperVoice", OnlyThePreloadedVoice)  # noqa: B010
    setattr(handler, "ensure_voice_exists", refuse_download)  # noqa: B010


def advertise_only(server: ModuleType, name: str) -> None:
    """Make wyoming-piper's Describe answer (Info) list only the served voice, not its whole
    bundled voices.json catalog, so a client's describe check fails on a wrong voice name.

    wyoming-piper 2.5.2 builds Info with `__main__._piper_info(args, voices_info)`, once at
    startup and again on every Describe; both calls look the name up in the module, so this
    wraps it there. If the voice is not in the Info at all, startup fails."""
    if not hasattr(server, SERVER_INFO_ATTRIBUTE):
        raise RuntimeError(
            f"{server.__name__} has no {SERVER_INFO_ATTRIBUTE}: not the pinned wyoming-piper 2.5.2"
        )
    build_info = getattr(server, SERVER_INFO_ATTRIBUTE)

    def only_the_served_voice(*args: Any, **kwargs: Any) -> Any:
        info = build_info(*args, **kwargs)
        for program in info.tts:
            program.voices = [voice for voice in program.voices if voice.name == name]
        advertised = [voice.name for program in info.tts for voice in program.voices]
        if advertised != [name]:
            raise RuntimeError(f"Describe would list {advertised}, not exactly {name!r}")
        return info

    setattr(server, SERVER_INFO_ATTRIBUTE, only_the_served_voice)


def wyoming_argv(uri: str, voice: str, data_dir: Path) -> list[str]:
    return [
        "wyoming-piper",
        "--voice",
        voice,
        "--uri",
        uri,
        "--data-dir",
        str(data_dir),
        "--download-dir",
        str(data_dir),
        "--use-cuda",
        "--samples-per-chunk",
        str(SAMPLES_PER_CHUNK),
    ]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="piper-service", description="The hub's wyoming-piper service (T-015)"
    )
    parser.add_argument("--check-install", action="store_true", help="check and exit")
    parser.add_argument("--uri", help="tcp://host:port to serve on")
    parser.add_argument("--voice", help="voice name, e.g. pl_PL-bass-high")
    parser.add_argument("--data-dir", type=Path, help="directory with <voice>.onnx(.json)")
    args = parser.parse_args(argv)
    if not args.check_install and not (args.uri and args.voice and args.data_dir):
        parser.error("--uri, --voice and --data-dir are required")
    return args


def main(
    argv: Sequence[str],
    *,
    load_voice: Callable[[Path, Any], Any],
    synthesize: Callable[[Any, str], float],
    handler: ModuleType,
    server: ModuleType,
    serve: Callable[[], None],
    version_of: Callable[[str], str] = importlib.metadata.version,
    ort: Any = onnxruntime,
    session_factory: Callable[[Path], Any] = create_session,
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)
    check_install(version_of, ort)
    if args.check_install:
        logger.info("install check passed: %s %s only", ORT_GPU, ORT_GPU_VERSION)
        return 0
    if WYOMING_PIPER_ARGS_ENV in os.environ:
        raise RuntimeError(f"{WYOMING_PIPER_ARGS_ENV} is set: pass options on the command line")
    model = args.data_dir / f"{args.voice}.onnx"
    config = args.data_dir / f"{args.voice}.onnx.json"
    for path in (model, config):
        if not path.is_file():
            raise FileNotFoundError(f"voice file {path} is missing (it is baked into the image)")
    start = time.monotonic()
    voice = load_voice(config, session_factory(model))
    logger.info("voice %s loaded in %.2f s", args.voice, time.monotonic() - start)
    warm_up(lambda text: synthesize(voice, text))
    check_warm(lambda text: synthesize(voice, text))
    install_voice(handler, voice, args.voice)
    advertise_only(server, args.voice)
    logger.info("warm in %.2f s; serving on %s", time.monotonic() - start, args.uri)
    sys.argv = wyoming_argv(args.uri, args.voice, args.data_dir)
    serve()
    return 0
