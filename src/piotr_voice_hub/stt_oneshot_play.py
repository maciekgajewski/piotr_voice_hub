"""Send one `play` and ignore every transcript.

This is the only process that plays the study URL. It writes the playback id to `PLAY`
after the ack and exits when that playback finishes. It does not send `speak`, `release`,
`stop`, or a second `play`. `tools/speak_test.py` answers every transcript and must not
be used for this study.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from piotr_voice_hub.config import STUDY_SATELLITE_ID
from piotr_voice_hub.stt.capture import ARM_NAME, PLAY_NAME
from piotr_voice_hub.stt_oneshot_client import ClientError, _preflight_directory, write_play_id

OPEN_TIMEOUT_SECONDS = 3.0
SEND_TIMEOUT_SECONDS = 10.0
PLAY_TIMEOUT_SECONDS = 120.0
MAX_MESSAGE_BYTES = 1024 * 1024
COMMAND_ID = "study-play"


def play_command(satellite: str, url: str) -> dict[str, Any]:
    return {
        "type": "play",
        "id": COMMAND_ID,
        "satellite": satellite,
        "data": {"url": url, "listen_after": True},
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-one-play", action="store_true")
    parser.add_argument("--satellite", required=True)
    parser.add_argument("--ws", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.confirm_one_play:
        raise ClientError("refusing to play without --confirm-one-play")
    if args.satellite != STUDY_SATELLITE_ID:
        raise ClientError(f"satellite must be {STUDY_SATELLITE_ID}")
    parts = urlsplit(args.url)
    if "@" in parts.netloc or parts.username or parts.password:
        raise ClientError("url must not contain user info")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ClientError("url must be http or https with a host")
    if not args.directory.is_absolute() or ".." in args.directory.parts:
        raise ClientError("directory must be an absolute path without '..'")
    _check_ws(args.ws)
    return args


def on_play_message(message: dict[str, Any], playback_id: str | None, satellite: str) -> str:
    """`ignore` for every transcript. `ack`, `finished`, or `error` for this one play."""
    kind = message.get("type")
    if kind == "ack" and message.get("reply_to") == COMMAND_ID and playback_id is None:
        data = message.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("playback_id"), str):
            raise ClientError("play ack has no playback_id")
        return "ack"
    if kind == "error" and message.get("reply_to") == COMMAND_ID:
        return "error"
    data = message.get("data")
    if (
        kind == "playback_finished"
        and playback_id is not None
        and message.get("satellite") == satellite
        and isinstance(data, dict)
        and data.get("playback_id") == playback_id
    ):
        return "finished"
    return "ignore"


def refuse_leftover_study_files(directory: Path) -> None:
    """`PLAY` or `ARM` from an earlier run must be gone before any audio is sent."""
    _preflight_directory(directory)
    for name in (PLAY_NAME, ARM_NAME):
        path = directory / name
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        raise ClientError(f"{name} is already present; not sending play")


async def run(args: argparse.Namespace) -> int:
    """One `play`, then wait for its `playback_finished`. Transcripts are not answered."""
    refuse_leftover_study_files(args.directory)
    sent = play_command(args.satellite, args.url)
    try:
        async with connect(
            args.ws,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            close_timeout=2,
            max_size=MAX_MESSAGE_BYTES,
        ) as socket:
            await asyncio.wait_for(
                socket.send(json.dumps(sent, ensure_ascii=False, separators=(",", ":"))),
                SEND_TIMEOUT_SECONDS,
            )
            playback_id: str | None = None
            deadline = time.monotonic() + PLAY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                raw = await asyncio.wait_for(socket.recv(), remaining)
                if not isinstance(raw, str):
                    raise ClientError("the hub sent a binary message")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ClientError("hub message is not an object")
                step = on_play_message(message, playback_id, args.satellite)
                if step == "ack":
                    data = message["data"]
                    found = data["playback_id"] if isinstance(data, dict) else None
                    if not isinstance(found, str):
                        raise ClientError("play ack has no playback_id")
                    playback_id = found
                    write_play_id(args.directory, playback_id)
                elif step == "error":
                    print(f"stt-oneshot-play: {message.get('data')}", file=sys.stderr)
                    return 1
                elif step == "finished":
                    print(
                        f"stt-oneshot-play: playback_id={playback_id} finished; no further command"
                    )
                    return 0
    except (ConnectionClosed, ClientError, OSError, json.JSONDecodeError) as error:
        print(f"stt-oneshot-play: stopped: {error.__class__.__name__}", file=sys.stderr)
        return 1
    print("stt-oneshot-play: playback did not finish", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except ClientError as error:
        print(f"stt-oneshot-play: {error}", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


def _check_ws(url: str) -> None:
    parts = urlsplit(url)
    if "@" in parts.netloc or parts.username or parts.password:
        raise ClientError("ws URL must not contain user info")
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise ClientError("expected a ws:// or wss:// URL")


if __name__ == "__main__":
    raise SystemExit(main())
