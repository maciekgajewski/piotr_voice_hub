"""T-028: the fixture generator refuses a non-loopback or production Piper URI."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).parent.parent / "tools" / "generate_audio_fixtures.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("generate_audio_fixtures", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "uri",
    [
        "tcp://127.0.0.1:10228",
        "tcp://localhost:10228",
        "tcp://[::1]:10228",
        "TCP://LocalHost:10228",
    ],
)
def test_loopback_piper_uri_is_accepted(uri: str) -> None:
    load_tool().check_piper_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "tcp://127.0.0.1:10201",
        "tcp://localhost:10201",
        "tcp://[::1]:10201",
    ],
)
def test_production_piper_port_is_rejected(uri: str) -> None:
    with pytest.raises(ValueError, match="10201"):
        load_tool().check_piper_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "tcp://0.0.0.0:10228",
        "tcp://8.8.8.8:10228",
        "tcp://192.168.1.10:10228",
        "tcp://[::]:10228",
    ],
)
def test_non_loopback_piper_host_is_rejected(uri: str) -> None:
    with pytest.raises(ValueError, match="localhost"):
        load_tool().check_piper_uri(uri)


def test_main_rejects_a_remote_uri_without_connecting() -> None:
    assert load_tool().main(["--piper-uri", "tcp://8.8.8.8:10228"]) == 2
