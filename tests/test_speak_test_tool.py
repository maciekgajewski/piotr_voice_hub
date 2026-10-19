"""T-017 follow-up: tools/speak_test.py against a fake hub WebSocket server."""

import asyncio
import importlib.util
import json
import socket
import sys
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
