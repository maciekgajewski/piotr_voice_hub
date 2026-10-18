"""Static checks for the T-003 GPU smoke setup (the GPU run itself is manual on piotr)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tools" / "gpu_smoke"


def test_dockerfile_uses_design_base_image() -> None:
    dockerfile = (ROOT / "docker" / "gpu-smoke.Dockerfile").read_text(encoding="utf-8")
    assert "FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04\n" in dockerfile


def test_requirements_are_pinned_with_hashes() -> None:
    for name in ("whisper-requirements.txt", "ort-requirements.txt"):
        text = (SMOKE / name).read_text(encoding="utf-8")
        requirements = [
            line for line in text.splitlines() if line and not line.startswith((" ", "#", "\t"))
        ]
        assert requirements, name
        for line in requirements:
            assert "==" in line, f"{name}: unpinned {line!r}"
        assert "--hash=sha256:" in text, name
