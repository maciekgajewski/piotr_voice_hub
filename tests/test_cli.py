import logging
from pathlib import Path

import pytest

from piotr_voice_hub.cli import EXIT_CONFIG_ERROR, main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config" / "hub.example.yaml"


def test_config_option_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 2
    assert "--config" in capsys.readouterr().err


def test_missing_config_file_exits_with_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert main(["--config", str(tmp_path / "missing.yaml")]) == EXIT_CONFIG_ERROR
    assert "config file not found" in caplog.text


def test_loads_and_logs_config_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert main(["--config", str(EXAMPLE), "--log-level", "DEBUG", "--api-port", "9000"]) == 0
    assert "4 satellite(s)" in caplog.text
    assert "API 0.0.0.0:9000" in caplog.text
    assert "CHANGE_ME" not in caplog.text
    assert "***" in caplog.text


def test_invalid_override_exits_with_error(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--config", str(EXAMPLE), "--api-port", "0"]) == EXIT_CONFIG_ERROR
    assert "api.port" in caplog.text
