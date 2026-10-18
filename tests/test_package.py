from importlib.metadata import version

import piotr_voice_hub


def test_package_imports() -> None:
    assert piotr_voice_hub.__name__ == "piotr_voice_hub"


def test_installed_version_matches_pyproject() -> None:
    assert version("piotr-voice-hub") == "0.1.0"
