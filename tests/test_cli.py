import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from piotr_voice_hub.cli import EXIT_CONFIG_ERROR, main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config" / "hub.example.yaml"


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """main() configures the root logger; undo that so tests stay independent."""
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    root.handlers[:] = handlers


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
    assert main(["--config", str(EXAMPLE), "--log-level", "DEBUG", "--api-port", "9000"]) == 0
    assert logging.getLogger().level == logging.DEBUG
    assert "4 satellite(s)" in caplog.text
    assert "API 0.0.0.0:9000" in caplog.text
    assert "CHANGE_ME" not in caplog.text
    assert "***" in caplog.text


def test_invalid_override_exits_with_error(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--config", str(EXAMPLE), "--api-port", "0"]) == EXIT_CONFIG_ERROR
    assert "api.port" in caplog.text


def test_config_log_level_applies_without_override() -> None:
    assert main(["--config", str(EXAMPLE)]) == 0
    assert logging.getLogger().level == logging.INFO


def test_non_utf8_config_exits_with_one_line_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.yaml"
    path.write_bytes(b"\xff\xfe")
    assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR
    assert len(caplog.records) == 1
    assert "not valid UTF-8" in caplog.records[0].getMessage()
