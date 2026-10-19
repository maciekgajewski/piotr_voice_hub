import http.server
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from piotr_voice_hub.cli import EXIT_CONFIG_ERROR, healthcheck, healthcheck_url, main
from piotr_voice_hub.config import HubConfig, load_config
from piotr_voice_hub.hub.main import EXIT_FAILED, QUIET_LOGGERS

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
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.NOTSET)


class FakeRun:
    def __init__(self, result: int = 0, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.configs: list[HubConfig] = []

    async def __call__(self, config: HubConfig) -> int:
        self.configs.append(config)
        if self.error is not None:
            raise self.error
        return self.result


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
    run = FakeRun()
    args = ["--config", str(EXAMPLE), "--log-level", "DEBUG", "--api-port", "9000"]
    assert main(args, run) == 0
    assert run.configs[0].api.port == 9000
    assert logging.getLogger().level == logging.DEBUG
    assert "4 satellite(s)" in caplog.text
    assert "API 0.0.0.0:9000" in caplog.text
    assert "CHANGE_ME" not in caplog.text
    assert "***" in caplog.text


def test_invalid_override_exits_with_error(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--config", str(EXAMPLE), "--api-port", "0"]) == EXIT_CONFIG_ERROR
    assert "api.port" in caplog.text


def test_config_log_level_applies_without_override() -> None:
    assert main(["--config", str(EXAMPLE)], FakeRun()) == 0
    assert logging.getLogger().level == logging.INFO


def test_non_utf8_config_exits_with_one_line_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "hub.yaml"
    path.write_bytes(b"\xff\xfe")
    assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR
    assert len(caplog.records) == 1
    assert "not valid UTF-8" in caplog.records[0].getMessage()


def test_runs_the_hub_and_returns_its_exit_code() -> None:
    run = FakeRun(result=EXIT_FAILED)
    assert main(["--config", str(EXAMPLE)], run) == EXIT_FAILED
    assert [s.id for s in run.configs[0].satellites][0] == "voice-pe-bedroom"


def test_an_unexpected_error_exits_1_with_a_critical_log(caplog: pytest.LogCaptureFixture) -> None:
    assert main(["--config", str(EXAMPLE)], FakeRun(error=OSError("address in use"))) == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert "hub failed to start or run" in record.getMessage()
    assert record.exc_info is not None


def test_library_loggers_are_quieted() -> None:
    assert main(["--config", str(EXAMPLE), "--log-level", "DEBUG"], FakeRun()) == 0
    assert logging.getLogger("aioesphomeapi").level == logging.ERROR
    assert logging.getLogger("piotr_voice_hub.hub.api.websockets").level == logging.WARNING
    assert logging.getLogger("faster_whisper").level == logging.WARNING
    assert logging.getLogger("piotr_voice_hub").getEffectiveLevel() == logging.DEBUG


# Healthcheck


def test_healthcheck_url_uses_loopback_for_a_wildcard_host() -> None:
    config = load_config(EXAMPLE)
    assert healthcheck_url(config) == "http://127.0.0.1:8750/healthz"
    lan = load_config(EXAMPLE, {"api.host": "192.168.0.10", "api.port": 9000})
    assert healthcheck_url(lan) == "http://192.168.0.10:9000/healthz"


class _Health(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(self.status if self.path == "/healthz" else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def _serve_health(status: int) -> http.server.HTTPServer:
    handler = type("Handler", (_Health,), {"status": status})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _config_for_port(tmp_path: Path, port: int) -> Path:
    path = tmp_path / "hub.yaml"
    text = EXAMPLE.read_text().replace("port: 8750", f"port: {port}")
    path.write_text(text)
    return path


def test_healthcheck_passes_on_200_and_fails_otherwise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for status, expected in ((200, 0), (503, 1)):
        server = _serve_health(status)
        try:
            assert healthcheck(
                ["--config", str(_config_for_port(tmp_path, server.server_port))]
            ) == (expected)
        finally:
            server.shutdown()
            server.server_close()
    assert "healthy" in capsys.readouterr().out


def test_healthcheck_fails_when_nothing_listens(tmp_path: Path) -> None:
    server = _serve_health(200)
    port = server.server_port
    server.shutdown()
    server.server_close()
    assert healthcheck(["--config", str(_config_for_port(tmp_path, port))]) == 1


def test_healthcheck_fails_on_an_invalid_config(tmp_path: Path) -> None:
    assert healthcheck(["--config", str(tmp_path / "missing.yaml")]) == 1
