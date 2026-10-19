"""T-017 test client: answer every `transcript_final` with a `speak` to that satellite.

Uses only the `websockets` package, which the hub image has. tools/ is not in the image, so pass
the file as `-c` (a TTY, so Ctrl-C reaches it and it quits cleanly):

    docker compose exec hub python -c "$(cat tools/speak_test.py)"
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --text "Dobranoc."
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --release

From a checkout with uv: `uv run --locked python tools/speak_test.py [--url ...] [--text ...]`.

It prints every message from the hub, and each command it sends prefixed with `>>> `, one per
line. By default it replies `Usłyszałem: <text>`; `--text` sends a fixed sentence instead, and
`--release` sends `release` (no reply). It never sets a volume. A dead hub is noticed by the
websockets keepalive (pings every 20 s).
Exit 0 on Ctrl-C, 1 if it cannot connect or the hub closes the connection.
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

DEFAULT_URL = "ws://localhost:8750/ws"
OPEN_TIMEOUT_SECONDS = 5.0


def command_for(
    message: dict[str, Any], text: str | None, release: bool, number: int
) -> dict[str, Any] | None:
    """The command answering `message`, or None if it is not a transcript_final."""
    if message.get("type") != "transcript_final":
        return None
    command: dict[str, Any] = {
        "type": "release" if release else "speak",
        "id": f"speak-test-{number}",
        "satellite": message["satellite"],
        "data": {},
    }
    if not release:
        reply = text if text is not None else f"Usłyszałem: {message['data']['text']}"
        command["data"] = {"text": reply}
    return command


async def run(url: str, text: str | None, release: bool) -> None:
    async with connect(url, open_timeout=OPEN_TIMEOUT_SECONDS) as websocket:
        print(f"connected to {url}", flush=True)
        sent = 0
        async for raw in websocket:
            print(raw, flush=True)
            command = command_for(json.loads(raw), text, release, sent + 1)
            if command is not None:
                sent += 1
                line = json.dumps(command, ensure_ascii=False)
                print(f">>> {line}", flush=True)
                await websocket.send(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"hub API (default {DEFAULT_URL})")
    reply = parser.add_mutually_exclusive_group()
    reply.add_argument("--text", help="speak this sentence instead of echoing the transcript")
    reply.add_argument("--release", action="store_true", help="send release instead of speak")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args.url, args.text, args.release))
    except KeyboardInterrupt:
        return 0
    except (OSError, TimeoutError, InvalidURI, InvalidHandshake) as error:
        print(f"cannot connect to {args.url}: {error!r}", file=sys.stderr)
        return 1
    except ConnectionClosed as error:
        print(f"the hub closed the connection: {error!r}", file=sys.stderr)
        return 1
    print("the hub closed the connection", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
