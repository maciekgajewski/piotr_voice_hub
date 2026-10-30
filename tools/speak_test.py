"""T-017/T-019 test client: answer every `transcript_final` with a `speak` to that satellite.

Uses only the `websockets` package, which the hub image has. tools/ is not in the image, so pass
the file as `-c` (a TTY, so Ctrl-C reaches it and it quits cleanly):

    docker compose exec hub python -c "$(cat tools/speak_test.py)"
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --text "Dobranoc."
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --release
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --long --stop-after 4
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --announce voice-pe-02
    docker compose exec hub python -c "$(cat tools/speak_test.py)" --stop voice-pe-02

From a checkout with uv: `uv run --locked python tools/speak_test.py [--url ...] [--text ...]`.

It prints every message from the hub, and each command it sends prefixed with `>>> `, one per
line. By default it replies `Usłyszałem: <text>`; `--text` sends a fixed sentence instead,
`--long` a Polish text of about 20 s, and `--release` sends `release` (no reply). It never sets
a volume. `--stop-after N` (N > 0) sends `stop` to the satellite N seconds after each `speak`
ack.

One-shot modes (T-019), which need no wake phrase: `--announce SATELLITE` sends one `speak`
(an announcement to an idle satellite; `--text`/`--long`/`--stop-after` apply) and exits 0
after its `playback_finished`, 1 on an error reply or if it has not finished within 120 s.
`--stop SATELLITE` sends one `stop`: exit 0 on its `ack`, 1 on an error reply (`busy` when
nothing plays).

A dead hub is noticed by the websockets keepalive (pings every 20 s).
Exit 0 on Ctrl-C, 1 if it cannot connect or the hub closes the connection.
"""

import argparse
import asyncio
import itertools
import json
import sys
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

DEFAULT_URL = "ws://localhost:8750/ws"
OPEN_TIMEOUT_SECONDS = 5.0
ANNOUNCE_TIMEOUT_SECONDS = 120.0  # --announce: from the speak to its playback_finished
ANNOUNCEMENT = "To jest test ogłoszenia z huba."
LONG_TEXT = (  # about 20 s with Piper
    "To jest długi test zatrzymywania odtwarzania. Za oknem powoli zapada zmrok, a w domu "
    "robi się cicho. Jutro rano ma być chłodno i pochmurno, po południu możliwy jest "
    "przelotny deszcz, a wieczorem temperatura spadnie do pięciu stopni. Jeśli nadal "
    "słyszysz to zdanie, polecenie stop nie zadziałało albo zostało wysłane za późno. "
    "Koniec długiego testu."
)


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


async def run(
    url: str,
    text: str | None,
    release: bool,
    *,
    announce: str | None = None,
    stop: str | None = None,
    stop_after: float | None = None,
) -> int | None:
    """Exit code of a one-shot mode, or None when the hub closed the connection."""
    numbers = itertools.count(1)
    speaks: dict[str, str] = {}  # id of each speak sent -> satellite
    timers: set[asyncio.Task[None]] = set()

    async def send(websocket: ClientConnection, command: dict[str, Any]) -> None:
        if command["type"] == "speak":
            speaks[command["id"]] = command["satellite"]
        line = json.dumps(command, ensure_ascii=False)
        print(f">>> {line}", flush=True)
        await websocket.send(line)

    def new(kind: str, satellite: str, data: dict[str, Any]) -> dict[str, Any]:
        number = next(numbers)
        return {"type": kind, "id": f"speak-test-{number}", "satellite": satellite, "data": data}

    async def stop_later(websocket: ClientConnection, satellite: str) -> None:
        assert stop_after is not None
        await asyncio.sleep(stop_after)
        await send(websocket, new("stop", satellite, {}))

    async with connect(url, open_timeout=OPEN_TIMEOUT_SECONDS) as websocket:
        print(f"connected to {url}", flush=True)
        first = None
        if announce is not None:
            first = new("speak", announce, {"text": text if text is not None else ANNOUNCEMENT})
        elif stop is not None:
            first = new("stop", stop, {})
        if first is not None:
            await send(websocket, first)
        playback_id = None
        # --announce: the whole wait, so a lost playback_finished cannot hang it (None: none).
        limit = ANNOUNCE_TIMEOUT_SECONDS if announce is not None else None
        try:
            async with asyncio.timeout(limit):
                async for raw in websocket:
                    print(raw, flush=True)
                    message = json.loads(raw)
                    kind, data = message.get("type"), message.get("data") or {}
                    if (
                        kind == "ack"
                        and message.get("reply_to") in speaks
                        and stop_after is not None
                    ):
                        satellite = speaks[message["reply_to"]]
                        timers.add(asyncio.create_task(stop_later(websocket, satellite)))
                    if first is None:
                        if kind == "transcript_final":
                            command = command_for(message, text, release, next(numbers))
                            assert command is not None
                            await send(websocket, command)
                    elif message.get("reply_to") == first["id"]:
                        if kind == "error":
                            return 1
                        if first["type"] == "stop":
                            return 0
                        playback_id = data.get("playback_id")
                    elif kind == "playback_finished" and data.get("playback_id") == playback_id:
                        return 0
        except TimeoutError:
            if limit is None or first is None:
                raise
            print(f"no playback_finished for {first['id']} within {limit:g} s", file=sys.stderr)
            return 1
        finally:
            for timer in timers:
                timer.cancel()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"hub API (default {DEFAULT_URL})")
    reply = parser.add_mutually_exclusive_group()
    reply.add_argument("--text", help="speak this sentence instead of echoing the transcript")
    reply.add_argument("--long", action="store_true", help="speak a Polish text of about 20 s")
    reply.add_argument("--release", action="store_true", help="send release instead of speak")
    once = parser.add_mutually_exclusive_group()
    once.add_argument("--announce", metavar="SATELLITE", help="speak once now, no wake needed")
    once.add_argument("--stop", metavar="SATELLITE", help="send stop once now")
    parser.add_argument(
        "--stop-after", type=float, metavar="SECONDS", help="send stop this long after a speak ack"
    )
    args = parser.parse_args(argv)
    if args.stop is not None and (
        args.text or args.long or args.release or args.stop_after is not None
    ):
        parser.error("--stop takes no other option than --url")
    if args.release and (args.announce is not None or args.stop_after is not None):
        parser.error("--release does not speak: no --announce or --stop-after")
    if args.stop_after is not None and not args.stop_after > 0:
        parser.error("--stop-after must be more than 0")
    text = LONG_TEXT if args.long else args.text
    try:
        code = asyncio.run(
            run(
                args.url,
                text,
                args.release,
                announce=args.announce,
                stop=args.stop,
                stop_after=args.stop_after,
            )
        )
    except KeyboardInterrupt:
        return 0
    except (OSError, TimeoutError, InvalidURI, InvalidHandshake) as error:
        print(f"cannot connect to {args.url}: {error!r}", file=sys.stderr)
        return 1
    except ConnectionClosed as error:
        print(f"the hub closed the connection: {error!r}", file=sys.stderr)
        return 1
    if code is not None:
        return code
    print("the hub closed the connection", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
