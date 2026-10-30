"""T-017 follow-up and T-019: tools/speak_test.py against a fake hub WebSocket server."""

import asyncio
import importlib.util
import json
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

TIMEOUT = 5.0
TOOL = Path(__file__).parent.parent / "tools" / "speak_test.py"


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("speak_test", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def final(satellite: str, text: str) -> str:
    data = {"utterance_id": "u1", "text": text, "raw_text": f"Ryszardzie, {text}"}
    return json.dumps({"type": "transcript_final", "satellite": satellite, "data": data})


async def fake_hub(*options: str) -> tuple[list[dict[str, Any]], int]:
    """Sends a status and two finals, acks each speak, then closes. The commands received and
    the tool's exit code."""
    tool = load_tool()
    received: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        await connection.send('{"type":"satellite","satellite":"voice-pe-02","data":{}}')
        for satellite, said in (("voice-pe-02", "która godzina"), ("voice-pe-03", "światło")):
            await connection.send(final(satellite, said))
            command = json.loads(await connection.recv())
            received.append(command)
            ack = {"type": "ack", "reply_to": command["id"], "satellite": satellite, "data": {}}
            await connection.send(json.dumps(ack))
        await connection.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        url = f"ws://127.0.0.1:{port}/ws"
        argv = ["--url", url, *options]
        code = await asyncio.wait_for(asyncio.to_thread(tool.main, argv), TIMEOUT)
    return received, code


def test_it_echoes_each_transcript_to_its_satellite(capsys: pytest.CaptureFixture[str]) -> None:
    received, code = asyncio.run(fake_hub())
    assert received == [
        {
            "type": "speak",
            "id": "speak-test-1",
            "satellite": "voice-pe-02",
            "data": {"text": "Usłyszałem: która godzina"},
        },
        {
            "type": "speak",
            "id": "speak-test-2",
            "satellite": "voice-pe-03",
            "data": {"text": "Usłyszałem: światło"},
        },
    ]
    assert code == 1  # the hub closed the connection
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("connected to ws://127.0.0.1:")
    assert [line.split(":", 1)[0] for line in lines[1:]] == [
        '{"type"',  # the status
        '{"type"',  # the first final
        '>>> {"type"',
        '{"type"',  # its ack
        '{"type"',
        '>>> {"type"',
        '{"type"',
    ]
    assert '>>> {"type": "speak", "id": "speak-test-1", "satellite": "voice-pe-02", ' in lines[3]
    assert "Usłyszałem" in lines[3]  # not escaped


def test_a_fixed_text(capsys: pytest.CaptureFixture[str]) -> None:
    received, _ = asyncio.run(fake_hub("--text", "Dobranoc."))
    assert [c["data"] for c in received] == [{"text": "Dobranoc."}] * 2  # and no volume


def test_release(capsys: pytest.CaptureFixture[str]) -> None:
    received, _ = asyncio.run(fake_hub("--release"))
    assert [(c["type"], c["satellite"], c["data"]) for c in received] == [
        ("release", "voice-pe-02", {}),
        ("release", "voice-pe-03", {}),
    ]


def test_no_hub_is_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # closed again: nothing listens there
    assert load_tool().main(["--url", f"ws://127.0.0.1:{port}/ws"]) == 1
    assert "cannot connect" in capsys.readouterr().err


def test_it_needs_only_websockets_and_the_standard_library() -> None:
    source = TOOL.read_text()
    imports = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imports - set(sys.stdlib_module_names) == {"websockets"}


# One-shot modes and stop (T-019)


async def scripted_hub(
    script: Callable[[ServerConnection, list[dict[str, Any]]], Awaitable[None]],
    *options: str,
    tool: ModuleType | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Runs `script` for the connection, then waits for the tool to leave. The commands
    received and the tool's exit code."""
    tool = load_tool() if tool is None else tool
    received: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        await connection.send('{"type":"satellite","satellite":"voice-pe-02","data":{}}')
        await script(connection, received)
        await connection.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        argv = ["--url", f"ws://127.0.0.1:{port}/ws", *options]
        code = await asyncio.wait_for(asyncio.to_thread(tool.main, argv), TIMEOUT)
    return received, code


async def receive(connection: ServerConnection, received: list[dict[str, Any]]) -> dict[str, Any]:
    command = json.loads(await connection.recv())
    received.append(command)
    return command


def reply(command: dict[str, Any], data: dict[str, Any], kind: str = "ack") -> str:
    message = {"type": kind, "reply_to": command["id"], "satellite": command["satellite"]}
    return json.dumps({**message, "data": data})


def finished(playback_id: str) -> str:
    data = {"playback_id": playback_id, "listening_for_follow_up": False}
    return json.dumps({"type": "playback_finished", "satellite": "voice-pe-02", "data": data})


async def acked_speak(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
    speak = await receive(connection, received)
    await connection.send(reply(speak, {"playback_id": "p1"}))
    await connection.send(finished("p0"))  # someone else's: ignored
    await connection.send(finished("p1"))


def test_announce_speaks_without_a_wake_and_exits_after_its_finish(
    capsys: pytest.CaptureFixture[str],
) -> None:
    received, code = asyncio.run(scripted_hub(acked_speak, "--announce", "voice-pe-03"))
    assert code == 0
    assert received == [
        {
            "type": "speak",
            "id": "speak-test-1",
            "satellite": "voice-pe-03",
            "data": {"text": load_tool().ANNOUNCEMENT},
        }
    ]
    assert capsys.readouterr().out.splitlines()[-1].startswith('{"type": "playback_finished"')


def test_announce_a_fixed_text(capsys: pytest.CaptureFixture[str]) -> None:
    received, code = asyncio.run(
        scripted_hub(acked_speak, "--announce", "voice-pe-02", "--text", "Obiad.")
    )
    assert code == 0 and received[0]["data"] == {"text": "Obiad."}


def test_an_announcement_refused_is_exit_1(capsys: pytest.CaptureFixture[str]) -> None:
    async def busy(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        speak = await receive(connection, received)
        error = {"code": "busy", "message": "voice-pe-02 is busy (listening): …"}
        await connection.send(reply(speak, error, "error"))

    _, code = asyncio.run(scripted_hub(busy, "--announce", "voice-pe-02"))
    assert code == 1
    assert '"busy"' in capsys.readouterr().out


def test_announce_long_with_stop_after(capsys: pytest.CaptureFixture[str]) -> None:
    times: list[float] = []

    async def stopped(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        speak = await receive(connection, received)
        await connection.send(reply(speak, {"playback_id": "p1"}))
        times.append(asyncio.get_running_loop().time())
        stop = await receive(connection, received)
        times.append(asyncio.get_running_loop().time())
        await connection.send(reply(stop, {}))
        await connection.send(finished("p1"))

    received, code = asyncio.run(
        scripted_hub(stopped, "--announce", "voice-pe-02", "--long", "--stop-after", "0.2")
    )
    assert code == 0
    assert [(c["type"], c["id"], c["satellite"]) for c in received] == [
        ("speak", "speak-test-1", "voice-pe-02"),
        ("stop", "speak-test-2", "voice-pe-02"),
    ]
    assert received[0]["data"] == {"text": load_tool().LONG_TEXT} and received[1]["data"] == {}
    assert len(load_tool().LONG_TEXT) > 250  # about 20 s
    assert times[1] - times[0] >= 0.15


def test_an_announcement_that_never_finishes_times_out(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def lost(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        speak = await receive(connection, received)
        await connection.send(reply(speak, {"playback_id": "p1"}))  # no playback_finished

    tool = load_tool()
    monkeypatch.setattr(tool, "ANNOUNCE_TIMEOUT_SECONDS", 0.2)
    _, code = asyncio.run(scripted_hub(lost, "--announce", "voice-pe-02", tool=tool))
    assert code == 1
    assert "no playback_finished for speak-test-1 within 0.2 s" in capsys.readouterr().err


def test_stop_alone_exits_0_on_its_ack(capsys: pytest.CaptureFixture[str]) -> None:
    async def acked(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        stop = await receive(connection, received)
        await connection.send(reply(stop, {}))

    _, code = asyncio.run(scripted_hub(acked, "--stop", "voice-pe-02"))
    assert code == 0


def test_stop_alone_exits_1_on_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    async def busy(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        stop = await receive(connection, received)
        error = {"code": "busy", "message": "voice-pe-02 is busy (idle): nothing is playing"}
        await connection.send(reply(stop, error, "error"))

    received, code = asyncio.run(scripted_hub(busy, "--stop", "voice-pe-02"))
    assert code == 1
    assert received == [
        {"type": "stop", "id": "speak-test-1", "satellite": "voice-pe-02", "data": {}}
    ]
    assert "nothing is playing" in capsys.readouterr().out


def test_replies_with_stop_after(capsys: pytest.CaptureFixture[str]) -> None:
    async def replied(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        await connection.send(final("voice-pe-03", "która godzina"))
        speak = await receive(connection, received)
        await connection.send(reply(speak, {"playback_id": "p1"}))
        stop = await receive(connection, received)
        await connection.send(reply(stop, {}))
        await connection.send(finished("p1"))
        await connection.close()

    received, code = asyncio.run(scripted_hub(replied, "--long", "--stop-after", "0.01"))
    assert code == 1  # the hub closed the connection
    assert [(c["type"], c["satellite"]) for c in received] == [
        ("speak", "voice-pe-03"),
        ("stop", "voice-pe-03"),
    ]
    assert received[0]["data"] == {"text": load_tool().LONG_TEXT}


@pytest.mark.parametrize(
    "options",
    [
        ("--stop", "voice-pe-02", "--text", "x"),
        ("--stop", "voice-pe-02", "--stop-after", "1"),
        ("--announce", "voice-pe-02", "--release"),
        ("--announce", "voice-pe-02", "--stop", "voice-pe-02"),
        ("--long", "--text", "x"),
        ("--stop-after", "-1"),
        ("--stop-after", "0"),
        ("--stop", "voice-pe-02", "--stop-after", "0"),
        ("--stop", "voice-pe-02", "--listen-after"),
        ("--release", "--listen-after"),
    ],
)
def test_bad_option_combinations(options: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as exit_:
        load_tool().main(list(options))
    assert exit_.value.code == 2


# Follow-up listening (T-020)


def test_listen_after_on_every_reply(capsys: pytest.CaptureFixture[str]) -> None:
    received, _ = asyncio.run(fake_hub("--listen-after", "--text", "Wziąć parasol?"))
    assert [c["data"] for c in received] == [{"text": "Wziąć parasol?", "listen_after": True}] * 2


def test_a_follow_up_is_answered_like_any_transcript(capsys: pytest.CaptureFixture[str]) -> None:
    async def follow_up(connection: ServerConnection, received: list[dict[str, Any]]) -> None:
        await connection.send(final("voice-pe-02", "czy pada"))
        speak = await receive(connection, received)
        await connection.send(reply(speak, {"playback_id": "p1"}))
        data = {"playback_id": "p1", "listening_for_follow_up": True}
        await connection.send(
            json.dumps({"type": "playback_finished", "satellite": "voice-pe-02", "data": data})
        )
        data = {"utterance_id": "u2", "text": "a jutro", "raw_text": "A jutro?", "follow_up": True}
        await connection.send(
            json.dumps({"type": "transcript_final", "satellite": "voice-pe-02", "data": data})
        )
        await receive(connection, received)
        await connection.close()

    received, code = asyncio.run(scripted_hub(follow_up, "--listen-after"))
    assert code == 1  # the hub closed the connection
    assert [c["data"] for c in received] == [
        {"text": "Usłyszałem: czy pada", "listen_after": True},
        {"text": "Usłyszałem: a jutro", "listen_after": True},
    ]


def test_announce_with_listen_after(capsys: pytest.CaptureFixture[str]) -> None:
    received, code = asyncio.run(
        scripted_hub(acked_speak, "--announce", "voice-pe-02", "--listen-after")
    )
    assert code == 0
    assert received[0]["data"] == {"text": load_tool().ANNOUNCEMENT, "listen_after": True}
