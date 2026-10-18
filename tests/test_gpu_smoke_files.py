"""Static checks for the T-003 GPU smoke setup (the GPU run itself is manual on piotr)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tools" / "gpu_smoke"


def read(name: str) -> str:
    return (SMOKE / name).read_text(encoding="utf-8")


def test_dockerfile_uses_design_base_image() -> None:
    dockerfile = (ROOT / "docker" / "gpu-smoke.Dockerfile").read_text(encoding="utf-8")
    assert "FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04\n" in dockerfile


def test_requirements_are_pinned_with_hashes() -> None:
    for name in ("whisper-requirements.txt", "ort-requirements.txt"):
        text = read(name)
        requirements = [
            line for line in text.splitlines() if line and not line.startswith((" ", "#", "\t"))
        ]
        assert requirements, name
        for line in requirements:
            assert "==" in line, f"{name}: unpinned {line!r}"
        assert "--hash=sha256:" in text, name


def test_critical_pins_survive_recompiles() -> None:
    # onnxruntime-gpu 1.28+ are CUDA 13 builds and silently fall back to CPU on CUDA 12.8.
    assert "\nonnxruntime-gpu==1.26.0 " in read("ort-requirements.txt")
    # av 16+ breaks faster-whisper 1.2.1 (metadata_errors argument).
    assert "\nav==15." in read("whisper-requirements.txt")


def test_cpu_and_gpu_onnxruntime_never_share_an_environment() -> None:
    assert "\nonnxruntime==" not in read("ort-requirements.txt")
    assert "\nonnxruntime-gpu==" not in read("whisper-requirements.txt")
