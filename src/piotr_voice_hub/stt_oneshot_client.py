"""Release the follow-up of one playback. It never plays audio.

Connect first. Write `ARM` only after `playback_finished` for the playback id in `PLAY`
(written by the play-only client) with `listening_for_follow_up` true. A transcript on the
study satellite is answered with `release` and nothing else. The capture file's utterance
must be the one that was released, unless cleanup left the text empty: then there is no
`transcript_final` and nothing to release.

Copy the pair before the hub exits. Shutdown deletes it. The next start deletes it when
`stt_capture.directory` is still configured, enabled or not. While the process stays up it
also deletes the pair after 1800 s. A crash is not erased until that next start, or until
the operator deletes the directory. There is no separate cleanup service.
"""

import argparse
import asyncio
import json
import os
import stat
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from piotr_voice_hub.config import STUDY_SATELLITE_ID
from piotr_voice_hub.stt.capture import (
    ARM_MAX_AGE_SECONDS,
    ARM_NAME,
    DIR_MODE,
    FILE_MODE,
    PLAY_NAME,
    arm_body,
    parse_arm,
)

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


def arm(directory: Path, playback_id: str, *, now: float | None = None) -> None:
    """Create `ARM` mode 0600 for one playback. A stale arm is replaced."""
    if not directory.is_absolute() or ".." in directory.parts:
        raise ClientError("directory must be an absolute path without '..'")
    _ensure_dir(directory)
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
    """Remove `ARM` if it is still there. The hub deletes capture files on shutdown."""
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
    _ensure_dir(directory)
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
    args = parser.parse_args(argv)
    if not args.arm or not args.confirm_one_shot:
        raise ClientError("refusing to run without both --arm and --confirm-one-shot")
    if args.satellite != STUDY_SATELLITE_ID:
        raise ClientError(f"satellite must be {STUDY_SATELLITE_ID}")
    if not 0 < args.timeout <= ARM_MAX_AGE_SECONDS:
        raise ClientError(f"timeout must be greater than 0 and at most {ARM_MAX_AGE_SECONDS:g} s")
    _check_ws(args.ws)
    if not args.directory.is_absolute() or ".." in args.directory.parts:
        raise ClientError("directory must be an absolute path without '..'")
    return args


async def run(args: argparse.Namespace) -> int:
    """Connect, then arm only for the playback named in `PLAY`."""
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
    try:
        return asyncio.run(run(args))
    except ClientError as error:
        print(f"stt-oneshot: {error}", file=sys.stderr)
        return 2


def _report(outcome: str, watch: StudyWatch) -> None:
    print(
        f"stt-oneshot: {outcome} playback_id={watch.playback_id}. "
        "Copy the pair before shutdown. The hub deletes it on shutdown and on the next "
        "start that still has stt_capture.directory set. A crash is not erased until that "
        "start, or until you delete the directory. This client played nothing."
    )


def _write_private(path: Path, body: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        os.write(fd, body)
    finally:
        os.close(fd)
    os.chmod(path, FILE_MODE)


def _ensure_dir(directory: Path) -> None:
    parent = directory.parent
    if not parent.is_dir():
        raise ClientError("directory parent does not exist")
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        os.mkdir(directory, DIR_MODE)
        os.chmod(directory, DIR_MODE)
        info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ClientError("directory must be a real directory, not a symlink")
    if info.st_uid != os.getuid() or (info.st_mode & 0o077) != 0 or (info.st_mode & 0o700) != 0o700:
        os.chmod(directory, DIR_MODE)
        info = os.lstat(directory)
    if info.st_uid != os.getuid() or (info.st_mode & 0o077) != 0 or (info.st_mode & 0o700) != 0o700:
        raise ClientError("directory must be mode 0700 and owned by this user")


def _check_ws(url: str) -> None:
    parts = urlsplit(url)
    if "@" in parts.netloc or parts.username or parts.password:
        raise ClientError("ws URL must not contain user info")
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ClientError("expected a ws:// or wss:// URL")


if __name__ == "__main__":
    raise SystemExit(main())
