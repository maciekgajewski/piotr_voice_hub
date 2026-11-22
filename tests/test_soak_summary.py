"""T-030: tools/soak_summary.py on a synthetic hub log (no IPs, no transcripts)."""

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).parent.parent / "tools" / "soak_summary.py"
DATA = Path(__file__).parent / "data" / "soak"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("soak_summary", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tmp_path: Path, *options: str) -> tuple[int, dict]:
    out = tmp_path / "summary.json"
    code = load_tool().main([str(DATA / "hub.log"), "--json", str(out), *options])
    return code, json.loads(out.read_text()) if out.exists() else {}


def test_counts_without_notes(tmp_path: Path) -> None:
    code, result = run(tmp_path)
    assert code == 0
    assert result["start"] == "2026-11-23 07:00:00 CET"  # log UTC shown as Europe/Warsaw
    assert result["hub_starts"] == ["2026-11-23 07:00:00"]
    assert result["tracebacks"] == 1
    assert len(result["errors"]) == 1
    assert result["tv_hours"] is None
    assert result["real_wakes"] is None  # N/A, not a fail
    pe02 = result["satellites"]["voice-pe-02"]
    assert pe02["counts"] == {
        "accepts": 3,
        "candidates_not_accepted": 1,
        "connected": 1,
        "no_request": 1,
        "reply_timeout": 3,
        "segments": 6,
        "still_busy": 1,
        "wake_alone": 1,
    }
    assert pe02["false_accepts"] == {"tv_unknown": 3}
    assert pe02["esphome_versions"] == ["2026.4.0"]
    assert pe02["reconnects"] == 0
    assert pe02["accept_latency_s"] == {"n": 3, "min": 0.5, "median": 0.6, "max": 0.8}
    assert pe02["stuck_processing"] == []
    pe03 = result["satellites"]["voice-pe-03"]
    assert pe03["counts"]["liveness"] == 1
    assert pe03["counts"]["connect_failed"] == 1
    assert "unavailable" not in pe03["counts"]  # the driver's own line is not counted twice
    assert pe03["reconnects"] == 1
    assert pe03["stuck_processing"] == ["2026-11-24 04:00:00"]  # 50 s to the reply timeout
    assert pe03["hourly"]["2026-11-24 03:00"]["liveness"] == 1


def test_notes_split_real_and_false_accepts(tmp_path: Path) -> None:
    code, result = run(tmp_path, "--notes", str(DATA / "notes.csv"))
    assert code == 0
    assert result["tv_hours"] == 4.5
    assert result["real_wakes"] == {
        "noted": 2,
        "hit": 1,
        "missed": [{"time": "2026-11-24 10:00", "satellite": "voice-pe-03", "hit": False}],
        "latency_s": {"n": 1, "min": 0.6, "median": 0.6, "max": 0.6},
    }
    pe02 = result["satellites"]["voice-pe-02"]
    # 18:05 is the noted real wake; 18:40 is during TV; 25 Nov has no notes at all.
    assert pe02["false_accepts"] == {"tv_on": 1, "tv_unknown": 1}
    assert pe02["false_accepts_per_tv_hour"] == round(1 / 4.5, 4)
    assert result["satellites"]["voice-pe-03"]["false_accepts"] == {"tv_off": 1}


def test_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert load_tool().main([str(DATA / "hub.log")]) == 0
    out = capsys.readouterr().out
    assert "real wakes: N/A (no notes)" in out
    assert "TV hours: unknown" in out


def test_missing_or_malformed_log(tmp_path: Path) -> None:
    tool = load_tool()
    assert tool.main([str(tmp_path / "absent.log")]) == 2
    junk = tmp_path / "junk.log"
    junk.write_text("not a hub log\n")
    assert tool.main([str(junk)]) == 2
    assert tool.main([str(DATA / "hub.log"), "--notes", str(tmp_path / "absent.csv")]) == 2


def test_fixture_is_synthetic() -> None:
    """The repo is public: no LAN addresses and no transcript text in the fixture."""
    text = (DATA / "hub.log").read_text()
    assert re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text) is None
    assert "Ryszard" not in text and "raw_text" not in text
