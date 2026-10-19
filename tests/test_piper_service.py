"""T-015: startup of the Piper service (tts/piper_service.py), with fakes for piper,
wyoming-piper and the GPU. The real thing is tests/test_piper_gpu.py."""

import faulthandler
import importlib.metadata
import logging
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path
from typing import Any, NoReturn

import pytest
import yaml
from onnxruntime.capi.onnxruntime_pybind11_state import Fail, InvalidArgument, RuntimeException

from piotr_voice_hub.tts import piper_service as ps

VOICE = "pl_PL-bass-high"
URI = "tcp://127.0.0.1:10201"
ROOT = Path(__file__).resolve().parent.parent
GPU_ORT = types.SimpleNamespace(__version__="1.26.0", get_device=lambda: "GPU")


def versions(**installed: str):
    def version_of(name: str) -> str:
        key = name.replace("-", "_")
        if key not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[key]

    return version_of


GPU_ONLY = versions(onnxruntime_gpu="1.26.0")


def test_install_with_only_onnxruntime_gpu_1_26_passes() -> None:
    ps.check_install(GPU_ONLY, GPU_ORT)


@pytest.mark.parametrize(("version", "device"), [("1.26.1", "GPU"), ("1.26.0", "CPU")])
def test_install_check_rejects_another_imported_onnxruntime(version: str, device: str) -> None:
    # Another dist (onnxruntime-openvino, ort-nightly-gpu, ...) can provide the same package.
    ort = types.SimpleNamespace(__version__=version, get_device=lambda: device)
    with pytest.raises(ps.InstallError, match="the imported onnxruntime"):
        ps.check_install(GPU_ONLY, ort)


def test_the_hub_onnxruntime_module_fails_the_module_check() -> None:
    with pytest.raises(ps.InstallError, match="the imported onnxruntime"):
        ps.check_install(GPU_ONLY)


@pytest.mark.parametrize(
    ("installed", "message"),
    [
        ({"onnxruntime_gpu": "1.26.0", "onnxruntime": "1.26.0"}, "runs on CPU"),
        ({"onnxruntime": "1.23.2"}, "runs on CPU"),
        ({}, "onnxruntime-gpu is not installed"),
        ({"onnxruntime_gpu": "1.28.0"}, "need 1.26.0"),
    ],
)
def test_install_check_rejects_anything_else(installed: dict[str, str], message: str) -> None:
    with pytest.raises(ps.InstallError, match=message):
        ps.check_install(versions(**installed))


def test_the_hub_environment_itself_fails_the_install_check() -> None:
    # The hub has the CPU onnxruntime (Silero VAD) and never onnxruntime-gpu (design §9).
    with pytest.raises(ps.InstallError):
        ps.check_install()


def test_the_cuda_provider_uses_the_heuristic_cudnn_search() -> None:
    assert ps.CUDA_PROVIDER == ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})


class FakeSession:
    def __init__(self, providers: list[str]) -> None:
        self.providers = providers

    def get_providers(self) -> list[str]:
        return self.providers


def test_session_on_cuda_is_created_with_our_options() -> None:
    calls: list[dict[str, Any]] = []

    def factory(model: str, **kwargs: Any) -> FakeSession:
        calls.append({"model": model, **kwargs})
        return FakeSession(["CUDAExecutionProvider", "CPUExecutionProvider"])

    available = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    session = ps.create_session(Path("/v/x.onnx"), lambda: available, factory)
    assert isinstance(session, FakeSession)
    (call,) = calls
    assert call["model"] == "/v/x.onnx"
    assert call["providers"] == [ps.CUDA_PROVIDER]
    assert isinstance(call["sess_options"], ps.onnxruntime.SessionOptions)


def test_no_cuda_provider_available_fails_before_loading() -> None:
    def factory(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("must not load")

    with pytest.raises(RuntimeError, match="not available"):
        ps.create_session(Path("x.onnx"), lambda: ["CPUExecutionProvider"], factory)


@pytest.mark.parametrize("providers", [["CPUExecutionProvider"], []])
def test_a_session_that_fell_back_to_cpu_fails(providers: list[str]) -> None:
    with pytest.raises(RuntimeError, match="not on CUDAExecutionProvider"):
        ps.create_session(
            Path("x.onnx"),
            lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
            lambda *a, **k: FakeSession(providers),
        )


def test_the_cpu_onnxruntime_cannot_create_a_cuda_session() -> None:
    # The hub's CPU onnxruntime: no CUDA EP, so the real defaults fail at once.
    with pytest.raises(RuntimeError, match="not available"):
        ps.create_session(Path("missing.onnx"))


def test_warm_up_synthesizes_each_text_in_order() -> None:
    seen: list[str] = []

    def synthesize(text: str) -> float:
        seen.append(text)
        return 1.5

    took = ps.warm_up(synthesize)
    assert seen == list(ps.WARMUP_TEXTS)
    assert len(took) == 3
    short, check, near_cap = ps.WARMUP_TEXTS
    assert len(short) < 40
    assert 180 <= len(check) <= 220
    assert check == ps.WARM_CHECK_TEXT
    # One sentence near the cap: a single ONNX call as long as a serving call can be.
    assert 900 <= len(near_cap) <= ps.MAX_TEXT_CHARS
    assert [c for c in near_cap if c in ".?!"] == ["."] and near_cap.endswith(".")


def test_warm_up_logs_each_time(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        ps.warm_up(lambda text: 2.0, texts=["a" * 949], clock=ticking_clock(1.0, 1.5))
    assert "warm-up: 949 characters, 2.00 s of audio in 0.50 s" in caplog.text


def test_warm_up_error_raises() -> None:
    def synthesize(text: str) -> float:
        raise ValueError("phonemizer broke")

    with pytest.raises(RuntimeError, match="phonemizer broke"):
        ps.warm_up(synthesize, texts=["a"])


def test_warm_up_without_audio_raises() -> None:
    with pytest.raises(RuntimeError, match="no audio"):
        ps.warm_up(lambda text: 0.0, texts=["a"])


class Exited(Exception):
    pass


def test_a_hung_warm_up_exits_the_process() -> None:
    release = threading.Event()
    codes: list[int] = []

    def exit_now(code: int) -> NoReturn:
        codes.append(code)
        raise Exited

    def synthesize(text: str) -> float:
        release.wait(5)
        return 1.0

    try:
        with pytest.raises(Exited):
            ps.warm_up(synthesize, texts=["a"], timeout=0.05, exit_now=exit_now)
    finally:
        release.set()
    assert codes == [1]


def ticking_clock(*times: float):
    values = iter(times)
    return lambda: next(values)


def test_a_warm_voice_passes_the_warm_check() -> None:
    seen: list[str] = []

    def synthesize(text: str) -> float:
        seen.append(text)
        return 12.0

    took = ps.check_warm(synthesize, clock=ticking_clock(10.0, 10.18))
    assert took == pytest.approx(0.18)
    assert seen == [ps.WARM_CHECK_TEXT]
    assert ps.WARM_LIMIT_SECONDS == 2.0


def test_the_warm_check_names_itself_in_its_logs(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        ps.check_warm(lambda text: 12.0, clock=ticking_clock(10.0, 10.18))
    assert f"warm check: {len(ps.WARM_CHECK_TEXT)} characters" in caplog.text
    assert "warm check passed" in caplog.text
    assert "warm-up" not in caplog.text


def test_a_hung_warm_check_names_itself(caplog: pytest.LogCaptureFixture) -> None:
    release = threading.Event()

    def exit_now(code: int) -> NoReturn:
        raise Exited

    def synthesize(text: str) -> float:
        release.wait(5)
        return 1.0

    try:
        with pytest.raises(Exited):
            ps._timed_synthesis(synthesize, "a", 0.05, time.monotonic, exit_now, "warm check")
    finally:
        release.set()
    assert "warm check synthesis did not finish within 0.05 s" in caplog.text


def test_a_slow_warm_voice_fails_the_warm_check() -> None:
    with pytest.raises(RuntimeError, match="not running on the GPU"):
        ps.check_warm(lambda text: 12.0, limit=1.0, clock=ticking_clock(10.0, 11.5))


class FakeVoice:
    config = types.SimpleNamespace(speaker_id_map={})

    def __init__(self, run: Any = None) -> None:
        self.calls: list[tuple[str, Any, Any]] = []
        self.run = run

    def synthesize_wav(self, text: str, wav_file: Any, syn_config: Any = None) -> None:
        self.calls.append((text, wav_file, syn_config))
        if self.run is not None:
            self.run()


class ExitRecorder:
    """A fake `os._exit`. In the watchdog's own thread it must not raise (`raises=False`)."""

    def __init__(self, raises: bool = True) -> None:
        self.codes: list[int] = []
        self.called = threading.Event()
        self.raises = raises

    def __call__(self, code: int) -> Any:
        self.codes.append(code)
        self.called.set()
        if self.raises:
            raise Exited


class Watchdog:
    """Records arm/disarm calls, with what the voice was doing at each."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def arm(self, timeout: float) -> None:
        self.events.append(f"arm {timeout:g}")

    def disarm(self) -> None:
        self.events.append("disarm")


def guarded(inner: FakeVoice, watchdog: Watchdog, **kwargs: Any) -> ps.GuardedVoice:
    return ps.GuardedVoice(inner, arm=watchdog.arm, disarm=watchdog.disarm, **kwargs)


def test_guarded_voice_passes_calls_and_config_through() -> None:
    watchdog = Watchdog()
    inner = FakeVoice(lambda: watchdog.events.append("synthesize"))
    exits = ExitRecorder()
    voice = guarded(inner, watchdog, timeout=0.05, exit_now=exits)
    voice.synthesize_wav("Dzień dobry.", "writer", "config")
    assert inner.calls == [("Dzień dobry.", "writer", "config")]
    assert voice.config is FakeVoice.config
    assert watchdog.events == ["arm 0.05", "synthesize", "disarm"]
    assert exits.codes == []


def test_the_default_watchdog_is_faulthandler() -> None:
    voice = ps.GuardedVoice(FakeVoice())
    assert voice._arm is ps._arm_watchdog
    assert voice._disarm is faulthandler.cancel_dump_traceback_later


def test_guarded_voice_refuses_text_over_the_cap() -> None:
    inner = FakeVoice()
    watchdog = Watchdog()
    voice = guarded(inner, watchdog, max_chars=10)
    voice.synthesize_wav("a" * 10, "writer")
    with pytest.raises(ps.TextTooLongError, match="11 characters, the limit per call is 10"):
        voice.synthesize_wav("a" * 11, "writer")
    assert [call[0] for call in inner.calls] == ["a" * 10]
    assert watchdog.events == ["arm 30", "disarm"]  # the refused call never arms it
    assert ps.MAX_TEXT_CHARS == 1000
    assert ps.SYNTHESIS_TIMEOUT_SECONDS == 30.0


@pytest.mark.parametrize("error", [ValueError("phonemizer"), Exited()])
def test_the_watchdog_is_disarmed_when_the_call_fails(error: Exception) -> None:
    watchdog = Watchdog()

    def fail() -> None:
        raise error

    with pytest.raises(type(error)):
        guarded(FakeVoice(fail), watchdog).synthesize_wav("Dzień dobry.", "writer")
    assert watchdog.events == ["arm 30", "disarm"]


@pytest.mark.parametrize("error", [Fail("CUDA failure 700"), RuntimeException("CUDA error")])
def test_an_onnxruntime_runtime_error_exits_the_process(error: Exception) -> None:
    def fail() -> None:
        raise error

    exits = ExitRecorder()
    voice = guarded(FakeVoice(fail), Watchdog(), exit_now=exits)
    with pytest.raises(Exited):
        voice.synthesize_wav("Dzień dobry.", "writer")
    assert exits.codes == [1]


@pytest.mark.parametrize("error", [InvalidArgument("bad input"), ValueError("phonemizer")])
def test_other_errors_go_back_to_the_client(error: Exception) -> None:
    def fail() -> None:
        raise error

    exits = ExitRecorder()
    voice = guarded(FakeVoice(fail), Watchdog(), exit_now=exits)
    with pytest.raises(type(error)):
        voice.synthesize_wav("Dzień dobry.", "writer")
    assert exits.codes == []


EXIT_SCRIPT = """
import logging, time
from onnxruntime.capi.onnxruntime_pybind11_state import Fail
from piotr_voice_hub.tts import piper_service as ps

class Voice:
    config = None
    def synthesize_wav(self, text, wav_file, syn_config=None):
        if {hang!r}:
            time.sleep(30)
        raise Fail("CUDA failure 700: an illegal memory access was encountered")

logging.basicConfig()
try:
    ps.GuardedVoice(Voice(), timeout=0.2).synthesize_wav("Dzień dobry.", None)
finally:
    print("not exited")
"""


@pytest.mark.parametrize(
    ("hang", "expected"),
    [
        # faulthandler's own dump: the timeout, then the stack of the stuck call.
        (True, ["Timeout (0:00:00.200000)!", "synthesize_wav"]),
        (False, ["CRITICAL:piper_service:", "onnxruntime failed during a synthesis"]),
    ],
    ids=["hang", "onnxruntime-error"],
)
def test_the_guard_really_exits_the_process(hang: bool, expected: list[str]) -> None:
    start = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(EXIT_SCRIPT.format(hang=hang))],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert done.returncode == 1
    for text in expected:
        assert text in done.stderr
    assert "not exited" not in done.stdout  # os._exit: no finally, no cleanup
    assert time.monotonic() - start < 15


def fake_handler() -> types.ModuleType:
    handler = types.ModuleType("wyoming_piper.handler")
    attributes = {"_VOICE": None, "_VOICE_NAME": None, "PiperVoice": object}
    attributes["ensure_voice_exists"] = lambda *args: None
    for name, value in attributes.items():
        setattr(handler, name, value)
    return handler


def test_install_voice_fills_the_cache_and_refuses_other_voices() -> None:
    handler = fake_handler()
    voice = object()
    ps.install_voice(handler, voice, VOICE)
    cached = getattr(handler, "_VOICE")  # noqa: B009
    assert isinstance(cached, ps.GuardedVoice)
    assert cached.voice is voice
    assert getattr(handler, "_VOICE_NAME") == VOICE  # noqa: B009
    loader = getattr(handler, "PiperVoice")  # noqa: B009
    with pytest.raises(RuntimeError, match="only the preloaded voice"):
        loader.load("/data/en_US-lessac-medium.onnx", use_cuda=True)
    ensure = getattr(handler, "ensure_voice_exists")  # noqa: B009
    with pytest.raises(RuntimeError, match="only the preloaded voice.*en_US-lessac-medium"):
        ensure("en_US-lessac-medium", ["/opt/piper/voices"], "/opt/piper/voices", {})


@pytest.mark.parametrize("missing", ps.HANDLER_ATTRIBUTES)
def test_install_voice_fails_on_another_wyoming_piper(missing: str) -> None:
    handler = fake_handler()
    delattr(handler, missing)
    with pytest.raises(RuntimeError, match="not the pinned wyoming-piper"):
        ps.install_voice(handler, object(), VOICE)


def catalog_info(*names: str) -> types.SimpleNamespace:
    voices = [types.SimpleNamespace(name=name) for name in names]
    return types.SimpleNamespace(tts=[types.SimpleNamespace(voices=voices)])


def fake_server(*names: str) -> types.ModuleType:
    server = types.ModuleType("wyoming_piper.__main__")
    calls: list[tuple[Any, ...]] = []

    def piper_info(*args: Any) -> types.SimpleNamespace:
        calls.append(args)
        return catalog_info(*names)  # a fresh Info per call, as wyoming-piper builds it

    setattr(server, ps.SERVER_INFO_ATTRIBUTE, piper_info)
    setattr(server, "calls", calls)  # noqa: B010
    return server


def advertised(server: types.ModuleType) -> list[str]:
    info = getattr(server, ps.SERVER_INFO_ATTRIBUTE)("args", {"voices": "info"})
    return [voice.name for program in info.tts for voice in program.voices]


def test_describe_lists_only_the_served_voice() -> None:
    server = fake_server("en_US-lessac-medium", VOICE, "pl_PL-gosia-medium")
    ps.advertise_only(server, VOICE)
    assert advertised(server) == [VOICE]
    assert advertised(server) == [VOICE]  # on every Describe, not only the first
    assert getattr(server, "calls") == [("args", {"voices": "info"})] * 2  # noqa: B009


def test_a_voice_missing_from_the_info_fails() -> None:
    server = fake_server("en_US-lessac-medium", "pl_PL-gosia-medium")
    ps.advertise_only(server, VOICE)
    with pytest.raises(RuntimeError, match="Describe would list"):
        advertised(server)


def test_advertise_only_fails_on_another_wyoming_piper() -> None:
    with pytest.raises(RuntimeError, match="not the pinned wyoming-piper"):
        ps.advertise_only(types.ModuleType("wyoming_piper.__main__"), VOICE)


def test_wyoming_argv_uses_cuda_and_never_updates_voices() -> None:
    argv = ps.wyoming_argv(URI, VOICE, Path("/opt/piper/voices"))
    assert argv == [
        "wyoming-piper",
        "--voice",
        VOICE,
        "--uri",
        URI,
        "--data-dir",
        "/opt/piper/voices",
        "--download-dir",
        "/opt/piper/voices",
        "--use-cuda",
        "--samples-per-chunk",
        "1024",
    ]
    assert "--update-voices" not in argv


def voice_dir(tmp_path: Path) -> Path:
    (tmp_path / f"{VOICE}.onnx").write_bytes(b"onnx")
    (tmp_path / f"{VOICE}.onnx.json").write_text("{}", encoding="utf-8")
    return tmp_path


def args(data_dir: Path) -> list[str]:
    return ["--uri", URI, "--voice", VOICE, "--data-dir", str(data_dir)]


class Recorder:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.voice = object()
        self.handler = fake_handler()
        self.server = fake_server("en_US-lessac-medium", VOICE)
        self.argv: list[str] = []

    def session(self, model: Path) -> str:
        self.steps.append(f"session {model.name}")
        return "session"

    def load_voice(self, config: Path, session: Any) -> object:
        self.steps.append(f"load {config.name} {session}")
        return self.voice

    def synthesize(self, voice: object, text: str) -> float:
        assert voice is self.voice
        assert getattr(self.handler, "_VOICE") is None  # noqa: B009 (not served before warm)
        self.steps.append("synthesize")
        return 1.0

    def serve(self) -> None:
        self.steps.append("serve")
        self.argv = list(ps.sys.argv)

    def main(self, argv: list[str], version_of: Any = GPU_ONLY) -> int:
        return ps.main(
            argv,
            load_voice=self.load_voice,
            synthesize=self.synthesize,
            handler=self.handler,
            server=self.server,
            serve=self.serve,
            version_of=version_of,
            ort=GPU_ORT,
            session_factory=self.session,
        )


@pytest.fixture(autouse=True)
def keep_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ps.sys, "argv", ["test"])
    monkeypatch.delenv(ps.WYOMING_PIPER_ARGS_ENV, raising=False)


def test_main_checks_loads_warms_installs_then_serves(tmp_path: Path) -> None:
    rec = Recorder()
    assert rec.main(args(voice_dir(tmp_path))) == 0
    assert rec.steps == [
        f"session {VOICE}.onnx",
        f"load {VOICE}.onnx.json session",
        "synthesize",
        "synthesize",
        "synthesize",  # the three warm-up texts
        "synthesize",  # the warm check
        "serve",
    ]
    assert getattr(rec.handler, "_VOICE").voice is rec.voice  # noqa: B009
    assert advertised(rec.server) == [VOICE]
    assert rec.argv == ps.wyoming_argv(URI, VOICE, tmp_path)


def test_main_check_install_only(tmp_path: Path) -> None:
    rec = Recorder()
    assert rec.main(["--check-install"]) == 0
    assert rec.steps == []
    with pytest.raises(ps.InstallError):
        rec.main(["--check-install"], version_of=versions(onnxruntime="1.23.2"))


def test_main_fails_before_loading_on_a_bad_install(tmp_path: Path) -> None:
    rec = Recorder()
    with pytest.raises(ps.InstallError):
        rec.main(args(voice_dir(tmp_path)), version_of=versions(onnxruntime_gpu="1.31.0"))
    assert rec.steps == []


def test_main_needs_the_baked_voice_files(tmp_path: Path) -> None:
    rec = Recorder()
    (tmp_path / f"{VOICE}.onnx").write_bytes(b"onnx")
    with pytest.raises(FileNotFoundError, match="onnx.json"):
        rec.main(args(tmp_path))
    assert rec.steps == []


def test_main_refuses_extra_args_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ps.WYOMING_PIPER_ARGS_ENV, "--update-voices")
    rec = Recorder()
    with pytest.raises(RuntimeError, match="WYOMING_PIPER_ARGS"):
        rec.main(args(voice_dir(tmp_path)))
    assert rec.steps == []


def test_main_requires_uri_voice_and_data_dir() -> None:
    rec = Recorder()
    with pytest.raises(SystemExit):
        rec.main(["--uri", URI])


def test_the_service_listens_on_loopback_only() -> None:
    # The hub (host network) is the only client; wyoming reads any announced message size, so
    # the service must not be reachable from the LAN (PR #18 review B2).
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    piper, hub = compose["services"]["piper"], compose["services"]["hub"]
    assert piper["network_mode"] == hub["network_mode"] == "host"
    command = piper["command"]
    assert command[command.index("--uri") + 1] == URI
    dockerfile = (ROOT / "docker" / "piper.Dockerfile").read_text(encoding="utf-8")
    assert f'CMD ["--uri", "{URI}",' in dockerfile
    assert "0.0.0.0" not in dockerfile
    assert "tcp://127.0.0.1:10201" in (ROOT / "config" / "hub.example.yaml").read_text("utf-8")
