"""T-015: startup of the Piper service (tts/piper_service.py), with fakes for piper,
wyoming-piper and the GPU. The real thing is tests/test_piper_gpu.py."""

import importlib.metadata
import threading
import types
from pathlib import Path
from typing import Any, NoReturn

import pytest

from piotr_voice_hub.tts import piper_service as ps

VOICE = "pl_PL-bass-high"


def versions(**installed: str):
    def version_of(name: str) -> str:
        key = name.replace("-", "_")
        if key not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return installed[key]

    return version_of


GPU_ONLY = versions(onnxruntime_gpu="1.26.0")


def test_install_with_only_onnxruntime_gpu_1_26_passes() -> None:
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
    assert len(took) == 2
    assert len(ps.WARMUP_TEXTS[0]) < 40
    assert 180 <= len(ps.WARMUP_TEXTS[1]) <= 220


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
    assert getattr(handler, "_VOICE") is voice  # noqa: B009
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


def test_wyoming_argv_uses_cuda_and_never_updates_voices() -> None:
    argv = ps.wyoming_argv("tcp://0.0.0.0:10201", VOICE, Path("/opt/piper/voices"))
    assert argv == [
        "wyoming-piper",
        "--voice",
        VOICE,
        "--uri",
        "tcp://0.0.0.0:10201",
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
    return ["--uri", "tcp://0.0.0.0:10201", "--voice", VOICE, "--data-dir", str(data_dir)]


class Recorder:
    def __init__(self) -> None:
        self.steps: list[str] = []
        self.voice = object()
        self.handler = fake_handler()
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
            serve=self.serve,
            version_of=version_of,
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
        "serve",
    ]
    assert getattr(rec.handler, "_VOICE") is rec.voice  # noqa: B009
    assert rec.argv == ps.wyoming_argv("tcp://0.0.0.0:10201", VOICE, tmp_path)


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
        rec.main(["--uri", "tcp://0.0.0.0:10201"])
