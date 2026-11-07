"""Release the follow-up of one playback. It never plays audio.

Connect first. Write `ARM` only after a host `at` job has been verified with `at -c`
(not `atq` alone) and after `playback_finished` for that playback has
`listening_for_follow_up` true. The job must be exactly
`find -P <directory> -mindepth 1 -delete` at the deadline the operator names, and
that deadline must still be in the future. If `at` is missing or any check is
uncertain, this process exits before `ARM`.

`at` is not installed on the preparation host. Nothing here installs it or claims the
study can be armed.

A transcript on the study satellite is answered with `release` and nothing else. The
capture file's utterance must be the one that was released, unless cleanup left the text
empty: then there is no `transcript_final` and nothing to release.

Deletion is the host job, including after a hub crash, and only when `atd` runs. A reboot
runs a due job only if `atd` starts. Teardown removes the directory contents before
`atrm`, and only then.
"""

import argparse
import asyncio
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from piotr_voice_hub.config import STUDY_SATELLITE_ID
from piotr_voice_hub.stt.capture import (
    ARM_MAX_AGE_SECONDS,
    ARM_NAME,
    FILE_MODE,
    PLAY_NAME,
    arm_body,
    cleanup_argv,
    cleanup_command,
    parse_arm,
)

_ATQ_LINE = re.compile(
    r"^(?P<id>[0-9]+)\t"
    r"(?P<dow>Sun|Mon|Tue|Wed|Thu|Fri|Sat) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(?P<day>[ \d]\d) "
    r"(?P<hms>\d{2}:\d{2}:\d{2}) "
    r"(?P<year>\d{4}) "
    r"[a-z] \S+\s*$"
)
_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
AT_TIMEOUT_SECONDS = 5.0

OPEN_TIMEOUT_SECONDS = 3.0
SEND_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.2
MAX_MESSAGE_BYTES = 1024 * 1024

Recv = Callable[[], Awaitable[str | bytes]]
Send = Callable[[str], Awaitable[None]]


class ClientError(Exception):
    """Refused before any capture, or the socket failed. `ARM` is removed."""


@dataclass
class StudyWatch:
    """Connected watcher. `ARM` is a later step, after the named playback has finished."""

    satellite: str
    playback_id: str | None = None
    armed: bool = False
    releases: dict[str, str] = field(default_factory=dict)  # utterance_id -> command id
    acked: set[str] = field(default_factory=set)
    transcripts: dict[str, dict[str, Any]] = field(default_factory=dict)
    follow_up_ready: set[str] = field(default_factory=set)

    def on_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind == "playback_finished" and message.get("satellite") == self.satellite:
            data = message.get("data")
            if isinstance(data, dict) and data.get("listening_for_follow_up") is True:
                playback_id = data.get("playback_id")
                if isinstance(playback_id, str):
                    self.follow_up_ready.add(playback_id)
            return None
        if kind == "transcript_final" and message.get("satellite") == self.satellite:
            data = message.get("data")
            if not isinstance(data, dict):
                return None
            utterance_id = data.get("utterance_id")
            if not isinstance(utterance_id, str) or utterance_id in self.releases:
                return None
            self.transcripts[utterance_id] = data
            command_id = f"rel-{len(self.releases) + 1}-{uuid.uuid4().hex[:8]}"
            self.releases[utterance_id] = command_id
            return release_command(self.satellite, command_id)
        reply_to = message.get("reply_to")
        if not isinstance(reply_to, str):
            return None
        utterance_id = next((uid for uid, cid in self.releases.items() if cid == reply_to), None)
        if utterance_id is None:
            return None
        if kind == "ack":
            self.acked.add(utterance_id)
        return None

    def should_arm(self) -> bool:
        return (
            not self.armed
            and self.playback_id is not None
            and self.playback_id in self.follow_up_ready
        )


def release_command(satellite: str, command_id: str) -> dict[str, Any]:
    return {"type": "release", "id": command_id, "satellite": satellite, "data": {}}


def verify_cleanup_job(
    job_id: str,
    directory: Path,
    deadline: datetime,
    atq_text: str,
    at_c_text: str,
    *,
    now: datetime,
) -> None:
    """Fail closed unless this job is the one future contents-only cleanup of `directory`.

    `atq` only supplies the id and the deadline. The command text comes from `at -c`.
    """
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ClientError("at job id must be digits")
    if deadline.tzinfo is not None or now.tzinfo is not None:
        raise ClientError("cleanup deadline must be a naive local time")
    when = _atq_deadline(atq_text, job_id)
    if when != deadline:
        raise ClientError("at job deadline does not match --cleanup-deadline")
    if when <= now:
        raise ClientError("at job deadline is not in the future")
    expected = cleanup_command(directory)
    cleanup_lines = [line for line in at_c_text.splitlines() if line.startswith(("rm ", "find "))]
    if cleanup_lines != [expected]:
        raise ClientError("at -c does not show exactly one cleanup of this directory")


def teardown(directory: Path, job_id: str, run: Callable[[list[str]], int]) -> None:
    """Delete the directory contents first. Cancel the job only after that succeeds.

    The directory itself stays, so a later container start can still bind it. A failed
    delete leaves the `at` job queued.
    """
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ClientError("at job id must be digits")
    if run(cleanup_argv(directory)) != 0:
        raise ClientError("capture files were not removed; the at job is still queued")
    if run(["atrm", job_id]) != 0:
        raise ClientError("files removed but atrm failed")


def arm(directory: Path, playback_id: str, *, now: float | None = None) -> None:
    """Create `ARM` mode 0600 for one playback. A stale arm is replaced."""
    _preflight_directory(directory)
    path = directory / ARM_NAME
    moment = time.time() if now is None else now
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        info = None
    if info is not None:
        age = moment - info.st_mtime
        stale = stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
        stale = stale or age < 0 or age > ARM_MAX_AGE_SECONDS
        if not stale:
            raise ClientError("an arm file is already present")
        os.unlink(path)
    _write_private(path, arm_body(playback_id))


def disarm(directory: Path) -> None:
    """Remove `ARM` if it is still there. The host `at` job deletes the contents."""
    try:
        os.unlink(directory / ARM_NAME)
    except FileNotFoundError:
        return


def read_play_id(directory: Path) -> str | None:
    """The playback id the play-only client wrote, or None if `PLAY` is not there yet."""
    path = directory / PLAY_NAME
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or (info.st_mode & 0o077):
        return None
    try:
        body = path.read_bytes()
    except OSError:
        return None
    return parse_arm(body)


def write_play_id(directory: Path, playback_id: str) -> None:
    """Publish the one playback id. The capture client reads it after it is connected."""
    _preflight_directory(directory)
    _write_private(directory / PLAY_NAME, arm_body(playback_id))


def capture_outcome(
    directory: Path, playback_id: str, transcripts: dict[str, dict[str, Any]], acked: set[str]
) -> str:
    """`waiting`, `empty`, `matched`, or `mismatch`.

    An empty cleaned text is the follow-up segment after cleanup removed it: there is no
    `transcript_final`. Any other text must be the released transcript's `raw_text` and the
    same utterance id.
    """
    files = [
        path
        for path in directory.glob("stt-oneshot-*.json")
        if path.is_file() and not path.is_symlink()
    ]
    if not files:
        return "waiting"
    if len(files) != 1:
        return "mismatch"
    try:
        body = json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "mismatch"
    if not isinstance(body, dict) or body.get("playback_id") != playback_id:
        return "mismatch"
    utterance_id = body.get("utterance_id")
    cleaned = body.get("cleaned_text")
    if not isinstance(utterance_id, str) or not isinstance(cleaned, str):
        return "mismatch"
    if cleaned == "":
        return "empty"
    transcript = transcripts.get(utterance_id)
    if transcript is None:
        return "waiting"
    if transcript.get("raw_text") != cleaned:
        return "mismatch"
    if utterance_id not in acked:
        return "waiting"
    return "matched"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="store_true", help="required: allow ARM after the playback")
    parser.add_argument(
        "--confirm-one-shot",
        action="store_true",
        help="required: this process may record one follow-up segment",
    )
    parser.add_argument("--satellite", required=True)
    parser.add_argument(
        "--ws", required=True, help="hub WebSocket, for example ws://127.0.0.1:8750/ws"
    )
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=ARM_MAX_AGE_SECONDS)
    parser.add_argument(
        "--at-job", default="", help="id of the host at job that deletes --directory"
    )
    parser.add_argument(
        "--cleanup-deadline",
        default="",
        help="local timestamp of that job, YYYY-MM-DDTHH:MM:SS",
    )
    parser.add_argument(
        "--teardown",
        action="store_true",
        help="rm the directory, then atrm; does not arm or connect",
    )
    args = parser.parse_args(argv)
    if args.teardown:
        if args.arm or args.confirm_one_shot:
            raise ClientError("teardown does not arm")
    elif not args.arm or not args.confirm_one_shot:
        raise ClientError("refusing to run without both --arm and --confirm-one-shot")
    if args.satellite != STUDY_SATELLITE_ID:
        raise ClientError(f"satellite must be {STUDY_SATELLITE_ID}")
    if not 0 < args.timeout <= ARM_MAX_AGE_SECONDS:
        raise ClientError(f"timeout must be greater than 0 and at most {ARM_MAX_AGE_SECONDS:g} s")
    _check_ws(args.ws)
    if (
        not args.directory.is_absolute()
        or ".." in args.directory.parts
        or any(part == ".." or any(c.isspace() for c in part) for part in args.directory.parts)
    ):
        raise ClientError("directory must be an absolute path without '..' or whitespace")
    if not re.fullmatch(r"[0-9]+", args.at_job):
        raise ClientError("refusing without a numeric --at-job")
    if not args.teardown:
        _parse_deadline(args.cleanup_deadline)
    return args


async def run(args: argparse.Namespace) -> int:
    """Verify the host job, connect, then arm only for the playback named in `PLAY`."""
    _preflight_directory(args.directory)
    atq_text, at_c_text = read_at_job(args.at_job)
    verify_cleanup_job(
        args.at_job,
        args.directory,
        _parse_deadline(args.cleanup_deadline),
        atq_text,
        at_c_text,
        now=datetime.now(),
    )
    watch = StudyWatch(args.satellite)
    try:
        async with connect(
            args.ws,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            close_timeout=2,
            max_size=MAX_MESSAGE_BYTES,
        ) as socket:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if watch.playback_id is None:
                    watch.playback_id = read_play_id(args.directory)
                if watch.should_arm():
                    assert watch.playback_id is not None
                    arm(args.directory, watch.playback_id)
                    watch.armed = True
                if watch.playback_id is not None:
                    outcome = capture_outcome(
                        args.directory, watch.playback_id, watch.transcripts, watch.acked
                    )
                    if outcome in ("empty", "matched"):
                        _report(outcome, watch)
                        return 0
                    if outcome == "mismatch":
                        print(
                            "stt-oneshot: capture does not match the released transcript",
                            file=sys.stderr,
                        )
                        return 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(socket.recv(), min(POLL_SECONDS, remaining))
                except TimeoutError:
                    continue
                if not isinstance(raw, str):
                    raise ClientError("the hub sent a binary message")
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ClientError("hub message is not an object")
                command = watch.on_message(loaded)
                if command is not None:
                    payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
                    await asyncio.wait_for(
                        socket.send(payload), min(SEND_TIMEOUT_SECONDS, remaining)
                    )
    except (ConnectionClosed, ClientError, OSError, json.JSONDecodeError) as error:
        print(f"stt-oneshot: stopped: {error.__class__.__name__}", file=sys.stderr)
        return 3 if watch.releases and not watch.acked else 1
    finally:
        disarm(args.directory)
    print("stt-oneshot: no matching capture before the deadline", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except ClientError as error:
        print(f"stt-oneshot: {error}", file=sys.stderr)
        return 2
    if args.teardown:
        try:
            teardown(args.directory, args.at_job, _run_command)
        except ClientError as error:
            print(f"stt-oneshot: {error}", file=sys.stderr)
            return 1
        return 0
    try:
        return asyncio.run(run(args))
    except ClientError as error:
        print(f"stt-oneshot: {error}", file=sys.stderr)
        return 2


def _report(outcome: str, watch: StudyWatch) -> None:
    print(
        f"stt-oneshot: {outcome} playback_id={watch.playback_id}. "
        "Copy the pair before the host at job runs. The hub does not delete it. "
        "Deletion happens only if atd runs that job. This client played nothing. "
        "Capture is not authorized until atd is available."
    )


def _write_private(path: Path, body: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.chmod(path, FILE_MODE)


def _preflight_directory(directory: Path) -> None:
    """The directory must already exist, mode 0700, owned by this user. Do not create it."""
    parent = directory.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as error:
        raise ClientError(f"directory parent is not usable: {error.strerror}") from None
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise ClientError("directory parent does not exist")
    try:
        info = os.lstat(directory)
    except OSError as error:
        raise ClientError(f"directory is not usable: {error.strerror}") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ClientError("directory must be a real directory, not a symlink")
    if info.st_uid != os.getuid() or (info.st_mode & 0o777) != 0o700:
        raise ClientError("directory must be mode 0700 and owned by this user")


def _parse_deadline(text: str) -> datetime:
    try:
        deadline = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise ClientError("cleanup deadline must be YYYY-MM-DDTHH:MM:SS") from None
    return deadline


def _atq_deadline(atq_text: str, job_id: str) -> datetime:
    found: datetime | None = None
    for line in atq_text.splitlines():
        if not line.strip():
            continue
        match = _ATQ_LINE.match(line)
        if match is None:
            raise ClientError("atq output is not understood; not arming")
        if match.group("id") != job_id:
            continue
        if found is not None:
            raise ClientError("atq lists the job more than once")
        hour, minute, second = (int(part) for part in match.group("hms").split(":"))
        found = datetime(
            int(match.group("year")),
            _MONTHS[match.group("mon")],
            int(match.group("day")),
            hour,
            minute,
            second,
        )
    if found is None:
        raise ClientError("atq does not list that job")
    return found


def read_at_job(job_id: str) -> tuple[str, str]:
    """`atq` and `at -c`. Missing `at`, a timeout, or a non-zero status is a refusal."""
    if not re.fullmatch(r"[0-9]+", job_id):
        raise ClientError("at job id must be digits")
    try:
        atq = subprocess.run(
            ["atq"],
            check=False,
            capture_output=True,
            text=True,
            timeout=AT_TIMEOUT_SECONDS,
        )
        listed = subprocess.run(
            ["at", "-c", job_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=AT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ClientError(f"at is not usable: {error.__class__.__name__}") from None
    if atq.returncode != 0 or listed.returncode != 0:
        raise ClientError("atq or at -c failed; not arming")
    return atq.stdout, listed.stdout


def _run_command(argv: list[str]) -> int:
    try:
        completed = subprocess.run(argv, check=False, timeout=AT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return completed.returncode


def _check_ws(url: str) -> None:
    parts = urlsplit(url)
    if "@" in parts.netloc or parts.username or parts.password:
        raise ClientError("ws URL must not contain user info")
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ClientError("expected a ws:// or wss:// URL")


if __name__ == "__main__":
    raise SystemExit(main())
