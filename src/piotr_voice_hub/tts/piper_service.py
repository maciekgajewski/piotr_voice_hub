"""Startup of the hub's own wyoming-piper service (T-015, design.md §9).

Runs inside the Piper image (docker/piper.Dockerfile), not in the hub: the image copies this
file next to docker/piper/entrypoint.py, which supplies the real piper and wyoming-piper. It
imports only the stdlib and onnxruntime, so the hub's tests cover it on the CPU onnxruntime.

Before wyoming-piper serves anything, `main()`:
1. checks that the only onnxruntime installed is onnxruntime-gpu 1.26.0 (also at build time:
   1.28+ are CUDA 13 builds, and the CPU `onnxruntime` next to it makes it run on CPU);
2. creates the voice's ONNX session itself, asking for the CUDA EP only, with cuDNN's
   HEURISTIC conv algorithm search, and fails unless CUDA is the session's first provider: when
   the CUDA EP cannot load, onnxruntime silently runs the whole model on CPU. (ORT's
   `session.disable_cpu_ep_fallback` cannot be used: this voice has 678 shape ops that ORT
   always places on CPU, next to 2036 nodes on CUDA, so the session would not start; T-015 log);
3. warms the voice up, each sentence bounded (T-003: the first inference took 48 s with the
   EXHAUSTIVE search), so the port opens, and the healthcheck turns green, only when warm;
4. hands the voice to wyoming-piper's one-voice cache and makes any other voice fail before
   it is downloaded or loaded (the voice is baked into the image, pinned by revision and hash);
5. runs wyoming-piper with `--use-cuda` and without `--update-voices`.

Any failure raises, so the process exits 1 and Docker restarts it.
"""

import argparse
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

CUDA = "CUDAExecutionProvider"
ORT_GPU = "onnxruntime-gpu"
ORT_GPU_VERSION = "1.26.0"
ORT_CPU = "onnxruntime"
CUDA_PROVIDER = (CUDA, {"cudnn_conv_algo_search": "HEURISTIC"})
SAMPLES_PER_CHUNK = 1024  # as the old project ran it: 46 ms at 22050 Hz
# One short and one long sentence (about 200 characters), so the first real request of either
# kind is not the first inference.
WARMUP_TEXTS = (
    "Dzień dobry, tu Ryszard.",
    "Jutro w Warszawie będzie pochmurno, rano około dwunastu stopni, a po południu przelotny "
    "deszcz i umiarkowany wiatr z zachodu, więc przed wyjściem z domu warto zabrać parasol i "
    "cieplejszą kurtkę.",
)
# Per warm-up sentence. T-003 measured 48 s for the first inference with EXHAUSTIVE search.
WARMUP_TIMEOUT_SECONDS = 120.0
# wyoming-piper 2.5.2 internals this module relies on (checked at startup).
HANDLER_ATTRIBUTES = ("_VOICE", "_VOICE_NAME", "PiperVoice", "ensure_voice_exists")
WYOMING_PIPER_ARGS_ENV = "WYOMING_PIPER_ARGS"

logger = logging.getLogger("piper_service")


class InstallError(RuntimeError):
    """The image does not have exactly onnxruntime-gpu 1.26.0."""


def check_install(version_of: Callable[[str], str] = importlib.metadata.version) -> None:
    """Fail unless onnxruntime-gpu is 1.26.0 and the CPU-only onnxruntime is absent."""
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


def _exit_now(code: int) -> NoReturn:
    os._exit(code)  # a wedged CUDA call cannot be cancelled; sys.exit would wait for it


def warm_up(
    synthesize: Callable[[str], float],
    texts: Sequence[str] = WARMUP_TEXTS,
    timeout: float = WARMUP_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    exit_now: Callable[[int], NoReturn] = _exit_now,
) -> list[float]:
    """Synthesize each text once; returns the seconds each took. An error raises; a call that
    runs past `timeout` exits the process at once."""
    took: list[float] = []
    for text in texts:
        result: dict[str, float | BaseException] = {}

        def run(text: str = text, result: dict[str, float | BaseException] = result) -> None:
            try:
                result["audio"] = synthesize(text)
            except BaseException as error:  # re-raised in the caller's thread below
                result["error"] = error

        start = clock()
        thread = threading.Thread(target=run, name="piper-warm-up", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            logger.critical("warm-up synthesis did not finish within %g s", timeout)
            exit_now(1)
        elapsed = clock() - start
        failure = result.get("error")
        if isinstance(failure, BaseException):
            raise RuntimeError(f"warm-up synthesis failed: {failure!r}") from failure
        audio = result["audio"]
        assert isinstance(audio, float)
        if audio <= 0:
            raise RuntimeError(f"warm-up synthesis gave no audio for {text!r}")
        logger.info("warm-up: %d characters, %.2f s of audio in %.2f s", len(text), audio, elapsed)
        took.append(elapsed)
    return took


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
    """Put the warmed voice into wyoming-piper's one-voice cache; any other voice fails
    before wyoming-piper downloads or loads it."""
    missing = [attr for attr in HANDLER_ATTRIBUTES if not hasattr(handler, attr)]
    if missing:
        raise RuntimeError(
            f"{handler.__name__} has no {missing}: not the pinned wyoming-piper 2.5.2"
        )
    setattr(handler, "_VOICE", voice)  # noqa: B010 (module globals of the pinned version)
    setattr(handler, "_VOICE_NAME", name)  # noqa: B010
    setattr(handler, "PiperVoice", OnlyThePreloadedVoice)  # noqa: B010
    setattr(handler, "ensure_voice_exists", refuse_download)  # noqa: B010


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
    serve: Callable[[], None],
    version_of: Callable[[str], str] = importlib.metadata.version,
    session_factory: Callable[[Path], Any] = create_session,
) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = parse_args(argv)
    check_install(version_of)
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
    install_voice(handler, voice, args.voice)
    logger.info("warm in %.2f s; serving on %s", time.monotonic() - start, args.uri)
    sys.argv = wyoming_argv(args.uri, args.voice, args.data_dir)
    serve()
    return 0
