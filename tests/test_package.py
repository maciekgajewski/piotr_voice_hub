import tomllib
from importlib.metadata import version
from pathlib import Path

import piotr_voice_hub

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_package_imports() -> None:
    assert piotr_voice_hub.__name__ == "piotr_voice_hub"


def test_installed_version_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert version(project["name"]) == project["version"]
