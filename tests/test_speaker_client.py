"""The speaker-recognition client (T-018) against fake HTTP servers on loopback, and once
against the real service code with a fake embedder."""

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np
import pytest

from piotr_voice_hub.config import SpeakerRecognitionConfig
from piotr_voice_hub.speaker import client as client_module
from piotr_voice_hub.speaker.client import HttpSpeakerRecognition, parse_response, parse_result
from piotr_voice_hub.speaker.interfaces import SpeakerMatch
from piotr_voice_hub.speaker.service import SpeakerServer

PCM = b"\x01\x00" * 16000  # 1 s
PROFILES = {"Maciek": "/profiles/maciek.npz", "Żaneta": "/profiles/zaneta.npz"}
OK = {
    "recognized_user": "Maciek",
    "confidence": 0.73456,
    "score": 0.7,
    "threshold": 0.45,
    "margin_to_second_best": 0.3,
    "profile": "/profiles/maciek.npz",
}

type Answer = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def http(status: str, body: bytes, headers: str = "") -> bytes:
    head = f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
    head += f"Content-Length: {len(body)}\r\n{headers}\r\n"
    return head.encode() + body


class FakeService:
    """Reads one request (head and Content-Length body) per connection, then `answer`s it."""

    def __init__(self, response: bytes = http("200 OK", json.dumps(OK).encode())) -> None:
        self.response = response
        self.delay = 0.0
        self.requests: list[tuple[str, dict[str, str], bytes]] = []
        self.server: asyncio.Server | None = None

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            request_line, *lines = head.rstrip("\r\n").split("\r\n")
            headers = {k.strip(): v.strip() for k, _, v in (x.partition(":") for x in lines)}
            body = await reader.readexactly(int(headers["Content-Length"]))
            self.requests.append((request_line, headers, body))
            await asyncio.sleep(self.delay)
            writer.write(self.response)
            await writer.drain()
        finally:
            writer.close()

    async def start(self) -> str:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def close(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


def make(
    url: str, timeout: float = 2.0, profiles: dict[str, str] = PROFILES
) -> HttpSpeakerRecognition:
    return HttpSpeakerRecognition(SpeakerRecognitionConfig(url, timeout, profiles))


async def recognize_with(service: FakeService, timeout: float = 2.0) -> SpeakerMatch | None:
    url = await service.start()
    try:
        return await make(url, timeout).recognize(PCM, "utt_1")
    finally:
        await service.close()


def test_a_match_sends_the_old_request_and_returns_the_speaker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, client_module.__name__)
    service = FakeService()
    assert asyncio.run(recognize_with(service)) == SpeakerMatch("Maciek", 0.735)
    [(request_line, headers, body)] = service.requests
    assert request_line == "POST /recognize-stream HTTP/1.1"
    assert body == PCM
    assert headers["Content-Length"] == str(len(PCM))
    assert headers["X-Audio-Sample-Rate"] == "16000"
    assert headers["X-Audio-Sample-Width"] == "2"
    assert headers["X-Audio-Channels"] == "1"
    assert json.loads(headers["X-Voice-Profiles"]) == PROFILES  # non-ASCII names escaped
    assert headers["X-Voice-Profiles"].isascii()
    assert "speaker utt_1: Maciek confidence 0.73 (score 0.700, threshold 0.450" in caplog.text


def test_no_match_is_none() -> None:
    answer = dict(OK, recognized_user=None, confidence=0.4, score=0.36)
    service = FakeService(http("200 OK", json.dumps(answer).encode()))
    assert asyncio.run(recognize_with(service)) is None
    assert len(service.requests) == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (http("500 Internal Server Error", b'{"error": "boom"}'), 'HTTP 500: {"error": "boom"}'),
        (http("400 Bad Request", b'{"error": "profile"}'), "HTTP 400"),
        (http("200 OK", b"<html>"), "answer is not JSON"),
        (http("200 OK", b"[1]"), "not a JSON object"),
        (http("200 OK", json.dumps(dict(OK, confidence="high")).encode()), "bad confidence"),
        (http("200 OK", json.dumps(dict(OK, confidence=1.5)).encode()), "not in [0, 1]"),
        (http("200 OK", json.dumps(dict(OK, score=True)).encode()), "bad score"),
        (http("200 OK", json.dumps(dict(OK, recognized_user="")).encode()), "bad recognized_user"),
        (http("200 OK", b"{}", "Transfer-Encoding: chunked\r\n"), "chunked"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}", "truncated answer"),
        (b"HTTP/1.1 200 OK\r\n", "incomplete HTTP answer"),
        (b"SSH-2.0-OpenSSH\r\n\r\n", "not an HTTP answer"),
        (b"", "incomplete HTTP answer (0 bytes)"),
        (http("200 OK", b" " * (client_module.MAX_RESPONSE_BYTES + 1)), "answer over"),
        # PR #27 review B1: these escaped recognize() as exceptions and stopped the hub.
        (http("200 OK", json.dumps(dict(OK, score=10**400)).encode()), "bad score"),  # Overflow
        (  # over the int string limit (4300 digits): ValueError, not a JSONDecodeError
            http(
                "200 OK",
                json.dumps(dict(OK, score=0))
                .replace('"score": 0', '"score": ' + "1" * 4301)
                .encode(),
            ),
            "answer is not JSON",
        ),
        (http("200 OK", b"[" * 60000), "answer is not JSON"),  # RecursionError
        (b"HTTP/1.1 200 OK\r\nContent-Length: \xb2\r\n\r\n{}", "bad Content-Length"),  # '²'
    ],
)
def test_a_bad_answer_is_none_and_one_warning(
    response: bytes, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert asyncio.run(recognize_with(FakeService(response))) is None
    [record] = [r for r in caplog.records if r.name == client_module.__name__ and r.levelno >= 30]
    assert record.levelno == logging.WARNING
    assert message in record.getMessage() and record.getMessage().endswith("; hint is null")


def test_any_other_failure_is_none_and_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The final catch-all (PR #27 review B1): an unforeseen error is still only a null hint."""

    def broken(body: bytes) -> object:
        raise KeyError("unforeseen")

    monkeypatch.setattr(client_module, "parse_result", broken)
    assert asyncio.run(recognize_with(FakeService())) is None
    [record] = [r for r in caplog.records if r.name == client_module.__name__ and r.levelno >= 30]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "speaker utt_1: recognition failed unexpectedly: KeyError('unforeseen'); hint is null"
    )


def test_a_slow_service_is_none_within_the_timeout(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        service = FakeService()
        service.delay = 0.5
        url = await service.start()
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            assert await make(url, 0.05).recognize(PCM, "utt_1") is None
            assert loop.time() - started < 0.4
        finally:
            await service.close()

    asyncio.run(run())
    assert "speaker utt_1: no answer within 0.05 s; hint is null" in caplog.text


def test_a_refused_connection_is_none(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> SpeakerMatch | None:
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()  # nothing listens on `port` now
        return await make(f"http://127.0.0.1:{port}").recognize(PCM, "utt_2")

    assert asyncio.run(run()) is None
    assert "speaker utt_2: recognition failed:" in caplog.text


def test_no_profiles_sends_nothing(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        service = FakeService()
        url = await service.start()
        try:
            assert await make(url, profiles={}).recognize(PCM, "utt_3") is None
            assert await make(url).recognize(b"", "utt_4") is None
        finally:
            await service.close()
        assert not service.requests

    asyncio.run(run())
    assert "speaker hint off: no speaker_recognition.profiles configured" in caplog.text


def test_a_cancelled_request_closes_the_connection() -> None:
    async def run() -> None:
        service = FakeService()
        service.delay = 0.5
        url = await service.start()
        try:
            task = asyncio.create_task(make(url).recognize(PCM, "utt_5"))
            async with asyncio.timeout(2):
                while not service.requests:
                    await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await service.close()

    asyncio.run(run())


def test_parse_response_and_result_directly() -> None:
    body = parse_response(http("200 OK", b'{"a": 1}'))
    assert body == b'{"a": 1}'
    # Without Content-Length: the body runs to EOF (Connection: close).
    assert parse_response(b"HTTP/1.0 200 OK\r\n\r\n{}") == b"{}"
    match, details = parse_result(json.dumps(OK).encode())
    assert match == SpeakerMatch("Maciek", 0.735)
    assert details == "score 0.700, threshold 0.450"


def test_end_to_end_with_the_real_service_code(tmp_path: Path) -> None:
    """The client and the service agree on the protocol (the fake embedder stands in for
    ECAPA, which only the image has)."""
    np.savez(tmp_path / "maciek.npz", profile_embedding=np.array([1.0, 0.0], dtype=np.float32))
    np.savez(tmp_path / "ola.npz", profile_embedding=np.array([0.0, 1.0], dtype=np.float32))
    heard: list[np.ndarray] = []

    def embed(samples: np.ndarray) -> np.ndarray:
        heard.append(samples)
        return np.array([0.9, 0.1], dtype=np.float32)

    server = SpeakerServer(("127.0.0.1", 0), embed, 0.45, tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        profiles = {"Maciek": str(tmp_path / "maciek.npz"), "Ola": "ola.npz"}
        match = asyncio.run(make(url, profiles=profiles).recognize(PCM, "utt_6"))
        outside = asyncio.run(make(url, profiles={"X": "/etc/hosts"}).recognize(PCM, "utt_7"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert match is not None and match.name == "Maciek" and match.confidence > 0.9
    assert outside is None
    assert len(heard) == 1 and heard[0].shape == (16000,)
