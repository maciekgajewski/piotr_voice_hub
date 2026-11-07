"""Arm one STT capture and release one follow-up transcript. Never plays audio.

The process connects out to the hub's existing WebSocket and sends only the `release`
command from design.md §6. It does not send speak or play, and it does not listen for
connections. Nothing is armed unless both `--arm` and `--confirm-one-shot` are set and
the satellite is voice-pe-03.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from piotr_voice_hub.config import STUDY_SATELLITE_ID
from piotr_voice_hub.stt.capture import (
    ARM_BODY,
    ARM_MAX_AGE_SECONDS,
    ARM_NAME,
    DIR_MODE,
    FILE_MODE,
    RETAIN_SECONDS,
)

OPEN_TIMEOUT_SECONDS = 3.0
SEND_TIMEOUT_SECONDS = 10.0
MAX_MESSAGE_BYTES = 1024 * 1024

Recv = Callable[[], Awaitable[str | bytes]]
Send = Callable[[str], Awaitable[None]]


class ClientError(Exception):
    """Refused before any capture, or the socket failed. The arm file is removed."""


@dataclass
class Watcher:
    """One follow-up final becomes one `release`. A second final sends nothing."""

    satellite: str
    command_id: str
    release_sent: bool = False
    release_acked: bool = False
    failed: bool = False
    saw_follow_up_final: bool = False
    text: str = ""

    def on_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        kind = message.get("type")
        if kind == "transcript_final" and message.get("satellite") == self.satellite:
            data = message.get("data")
            if not isinstance(data, dict) or data.get("follow_up") is not True:
                return None
            self.saw_follow_up_final = True
            if self.release_sent:
                return None
            text = data.get("text")
            self.text = text if isinstance(text, str) else ""
            self.release_sent = True
            return release_command(self.satellite, self.command_id)
        if message.get("reply_to") != self.command_id:
            return None
        if kind == "ack":
            self.release_acked = True
        elif kind == "error":
            self.failed = True
        return None

    @property
    def done(self) -> bool:
        return self.release_acked or self.failed


def release_command(satellite: str, command_id: str) -> dict[str, Any]:
    return {"type": "release", "id": command_id, "satellite": satellite, "data": {}}


def arm(directory: Path, *, now: float | None = None) -> None:
    """Create `ARM` mode 0600. A stale arm is replaced. A fresh one is an error."""
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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    try:
        os.write(fd, ARM_BODY)
    finally:
        os.close(fd)
    os.chmod(path, FILE_MODE)


def disarm(directory: Path) -> None:
    """Remove `ARM` if it is still there. Capture files are left for the hub's retention."""
    try:
        os.unlink(directory / ARM_NAME)
    except FileNotFoundError:
        return


async def watch(recv: Recv, send: Send, watcher: Watcher, timeout: float) -> Watcher:
    """Read until `release` is acked, the peer fails it, or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while not watcher.done:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(recv(), remaining)
        except TimeoutError:
            break
        if not isinstance(raw, str):
            raise ClientError("the hub sent a binary message")
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            raise ClientError("hub message is not an object")
        command = watcher.on_message(loaded)
        if command is None:
            continue
        send_budget = min(SEND_TIMEOUT_SECONDS, max(0.1, deadline - time.monotonic()))
        payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        await asyncio.wait_for(send(payload), send_budget)
    return watcher


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="store_true", help="required: write the one-shot ARM file")
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
        raise ClientError(f"timeout must be from 0 to {ARM_MAX_AGE_SECONDS:g} seconds")
    _check_ws(args.ws)
    if not args.directory.is_absolute() or ".." in args.directory.parts:
        raise ClientError("directory must be an absolute path without '..'")
    return args


async def run(args: argparse.Namespace) -> int:
    arm(args.directory)
    watcher = Watcher(args.satellite, f"oneshot-{uuid.uuid4().hex[:8]}")
    try:
        try:
            await _connect_and_watch(args.ws, watcher, args.timeout)
        except (ConnectionClosed, ClientError, OSError, json.JSONDecodeError) as error:
            print(f"stt-oneshot: stopped: {error.__class__.__name__}", file=sys.stderr)
            return 3 if watcher.release_sent and not watcher.release_acked else 1
    finally:
        disarm(args.directory)
    if watcher.release_acked:
        shown = watcher.text if len(watcher.text) <= 200 else watcher.text[:200] + "…"
        print(
            f"stt-oneshot: released one follow-up on {args.satellite} "
            f"text={shown!r} directory={args.directory} "
            f"files removed after {RETAIN_SECONDS:g} s; this client played nothing"
        )
        return 0
    if watcher.saw_follow_up_final:
        print("stt-oneshot: saw a follow-up transcript but release was not acked", file=sys.stderr)
        return 1
    print("stt-oneshot: no follow-up transcript before the deadline", file=sys.stderr)
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


async def _connect_and_watch(url: str, watcher: Watcher, timeout: float) -> None:
    async with connect(
        url,
        open_timeout=OPEN_TIMEOUT_SECONDS,
        close_timeout=2,
        max_size=MAX_MESSAGE_BYTES,
    ) as socket:

        async def recv() -> str | bytes:
            return await socket.recv()

        async def send(payload: str) -> None:
            await socket.send(payload)

        await asyncio.wait_for(watch(recv, send, watcher, timeout), timeout + 1)


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
