"""T-030: tools/soak_monitor.sh sample mode survives failing tools (PR #45 review B1).

Runs the real script with stub commands first on PATH; nothing touches docker, the GPU or
the network.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "tools" / "soak_monitor.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def run_sample(tmp_path: Path, du: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "docker", "echo 'Cannot connect to the Docker daemon' >&2; exit 1")
    stub(bin_dir, "curl", "echo '<html><body>502 Bad Gateway</body></html>'")
    stub(bin_dir, "nvidia-smi", "echo '999999, 100'")  # a pid with no /proc entry
    stub(bin_dir, "grep", "exit 1")  # the cgroup lookup fails (host process, pid gone)
    stub(bin_dir, "du", du)
    stub(bin_dir, "df", "exit 1")
    stub(bin_dir, "tmux", f'echo "$@" >> "{tmp_path}/tmux.calls"')
    out = tmp_path / "probe"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SOAK_INTERVAL": "0",
        "SOAK_RES_EVERY": "1",
        "SOAK_ITERATIONS": "2",
    }
    assert BASH is not None
    result = subprocess.run(
        [BASH, str(SCRIPT), "sample", str(out)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, out


def test_syntax() -> None:
    assert BASH is not None
    subprocess.run([BASH, "-n", str(SCRIPT)], check=True, timeout=10)


def test_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck not installed")
    subprocess.run([shellcheck, str(SCRIPT)], check=True, timeout=30)


def test_sample_survives_failing_tools(tmp_path: Path) -> None:
    result, out = run_sample(tmp_path, du="exit 1")
    assert result.returncode == 0, result.stderr
    health = (out / "health.jsonl").read_text().splitlines()
    assert len(health) == 2  # the second sample was written after the first one's failures
    res = (out / "res.csv").read_text()
    assert res.count("missing,du failed") == 2  # logged on every iteration
    assert res.count("missing,df failed") == 2
    assert res.count(",disk,0 MB,0%") == 2
    assert ",gpu,999999,host,100" in res
    assert not (out / "ABORT").exists()
    assert not (tmp_path / "tmux.calls").exists()


def test_guard_still_trips(tmp_path: Path) -> None:
    result, out = run_sample(tmp_path, du='printf "999\\t%s\\n" "$2"')
    assert result.returncode == 3
    assert "ABORT disk: dir 999 MB" in (out / "ABORT").read_text()
    assert (tmp_path / "tmux.calls").read_text().strip() == "kill-session -t t030"
    assert len((out / "health.jsonl").read_text().splitlines()) == 1
