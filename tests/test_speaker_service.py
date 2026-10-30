"""The speaker-recognition service (T-018) with a fake embedder, on a real loopback socket."""

import http.client
import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from piotr_voice_hub.speaker import service
from piotr_voice_hub.speaker.service import SpeakerServer

PCM = b"\x00\x10" * 16000  # 1 s


class FakeEmbed:
    def __init__(self) -> None:
        self.vector = np.array([1.0, 0.2, 0.0], dtype=np.float32)
        self.inputs: list[np.ndarray] = []
        self.error: Exception | None = None

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        self.inputs.append(samples)
        if self.error is not None:
            raise self.error
        return self.vector


def save(path: Path, vector: list[float]) -> Path:
    np.savez(path, profile_embedding=np.array(vector, dtype=np.float32))
    return path


@pytest.fixture
def profiles_dir(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    root.mkdir()
    save(root / "maciek.npz", [2.0, 0.0, 0.0])  # not normalized, like any real profile
    save(root / "ola.npz", [0.0, 1.0, 0.0])
    return root


@pytest.fixture
def embed() -> FakeEmbed:
    return FakeEmbed()


@pytest.fixture
def server(profiles_dir: Path, embed: FakeEmbed) -> Iterator[SpeakerServer]:
    server = SpeakerServer(("127.0.0.1", 0), embed, 0.45, profiles_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(
    server: SpeakerServer,
    method: str = "POST",
    path: str = "/recognize-stream",
    body: bytes | None = PCM,
    headers: dict[str, str] | None = None,
    profiles: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    all_headers = {
        "X-Audio-Sample-Rate": "16000",
        "X-Audio-Sample-Width": "2",
        "X-Audio-Channels": "1",
        "X-Voice-Profiles": json.dumps(
            {"Maciek": "maciek.npz", "Ola": "ola.npz"} if profiles is None else profiles
        ),
    }
    all_headers.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def absolute(server: SpeakerServer, name: str) -> str:
    return str(server.profiles_dir / name)


def test_health(server: SpeakerServer) -> None:
    assert request(server, "GET", "/health", body=None) == (200, {"status": "ok"})
    assert request(server, "GET", "/nope", body=None)[0] == 404
    assert request(server, path="/recognize")[0] == 404  # the old WAV endpoint is gone


def test_the_top_profile_at_or_above_the_threshold_is_recognized(
    server: SpeakerServer, embed: FakeEmbed, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, "speaker_service")
    profiles = {"Maciek": absolute(server, "maciek.npz"), "Ola": "ola.npz"}
    status, answer = request(server, profiles=profiles)
    assert status == 200
    score = 1 / np.sqrt(1.04)
    assert answer["recognized_user"] == "Maciek"
    assert answer["score"] == pytest.approx(score, abs=1e-6)
    assert answer["threshold"] == 0.45
    assert answer["confidence"] == pytest.approx(0.5 + (score - 0.45) / 0.55 * 0.5, abs=1e-6)
    assert answer["margin_to_second_best"] == pytest.approx(score - 0.2 / np.sqrt(1.04), abs=1e-6)
    assert answer["profile"] == absolute(server, "maciek.npz")
    # The PCM as the old embedder read it: 16-bit little-endian, scaled to [-1, 1).
    [samples] = embed.inputs
    assert samples.dtype == np.float32 and samples.shape == (16000,)
    assert samples[0] == pytest.approx(0x1000 / 32768)
    assert "Maciek: top Maciek score 0.981 (threshold 0.45), 1.0 s of audio" in caplog.text


def test_below_the_threshold_is_no_match(server: SpeakerServer, embed: FakeEmbed) -> None:
    embed.vector = np.array([0.4, 0.3, 1.0], dtype=np.float32)
    status, answer = request(server, profiles={"Maciek": "maciek.npz", "Ola": "ola.npz"})
    assert status == 200
    assert answer["recognized_user"] is None
    assert answer["score"] == pytest.approx(0.4 / np.sqrt(1.25), abs=1e-6)
    assert isinstance(answer["confidence"], float) and answer["confidence"] < 0.5
    assert answer["profile"] == absolute(server, "maciek.npz")


def test_a_score_exactly_at_the_threshold_is_recognized(tmp_path: Path) -> None:
    """At or above the threshold, as before (PR #27 review N3)."""
    profiles = {"Maciek": (tmp_path / "maciek.npz", service.normalize(np.array([1.0, 0.0])))}
    embedding = np.array([0.6, 0.8], dtype=np.float32)
    _, answer = service.recognize(embedding, profiles, 0.0)
    score = answer["score"]
    name, at = service.recognize(embedding, profiles, score)
    assert (name, at["recognized_user"], at["confidence"]) == ("Maciek", "Maciek", 0.5)
    _, below = service.recognize(embedding, profiles, float(np.nextafter(score, 1.0)))
    assert below["recognized_user"] is None  # the score just under a higher threshold


@pytest.mark.parametrize(
    ("score", "confidence"),
    [(-0.3, 0.0), (0.0, 0.0), (0.225, 0.25), (0.45, 0.5), (0.725, 0.75), (1.0, 1.0)],
)
def test_score_to_confidence_is_the_old_mapping(score: float, confidence: float) -> None:
    assert service.score_to_confidence(score, 0.45) == pytest.approx(confidence)


@pytest.mark.parametrize(
    ("headers", "profiles", "message"),
    [
        ({"X-Audio-Sample-Rate": "22050"}, None, "X-Audio-Sample-Rate must be 16000"),
        ({"X-Audio-Sample-Width": "4"}, None, "X-Audio-Sample-Width must be 2"),
        ({"X-Audio-Channels": "2"}, None, "X-Audio-Channels must be 1"),
        ({"X-Voice-Profiles": "{nope"}, None, "must be valid JSON"),
        ({}, {}, "non-empty JSON object"),
        ({"X-Voice-Profiles": '{"Maciek": 1}'}, None, "map user names to profile paths"),
        ({}, {"X": "/etc/passwd"}, "'X': /etc/passwd is not under"),
        ({}, {"X": "../outside.npz"}, "is not under"),
        ({}, {"X": "missing.npz"}, "profile 'X': cannot load"),
    ],
)
def test_bad_requests_are_400(
    server: SpeakerServer,
    embed: FakeEmbed,
    headers: dict[str, str],
    profiles: dict[str, str] | None,
    message: str,
) -> None:
    status, answer = request(server, headers=headers, profiles=profiles)
    assert status == 400
    assert message in str(answer["error"])
    assert not embed.inputs


def test_a_profile_without_the_embedding_key_is_400(server: SpeakerServer) -> None:
    np.savez(server.profiles_dir / "wrong.npz", other=np.zeros(3))
    status, answer = request(server, profiles={"X": "wrong.npz"})
    assert status == 400 and "cannot load" in str(answer["error"])


def test_the_audio_length_is_bounded(server: SpeakerServer, embed: FakeEmbed) -> None:
    status, answer = request(server, body=b"\x00\x00" * 1000)  # 62.5 ms
    assert status == 400 and "at least 0.1 s" in str(answer["error"])
    status, answer = request(server, body=b"\x00" * 3201)
    assert status == 400 and "16-bit audio" in str(answer["error"])
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    connection.putrequest("POST", "/recognize-stream")
    connection.putheader("Content-Length", str(service.MAX_BODY_BYTES + 2))
    connection.endheaders()  # nothing sent: refused on the header alone
    response = connection.getresponse()
    assert response.status == 400 and b"over 60 s of audio" in response.read()
    connection.close()
    assert not embed.inputs


@pytest.mark.parametrize("length", [None, "\u00b2", "-1", "1e3"])
def test_content_length_is_required_as_ascii_digits(
    server: SpeakerServer, length: str | None
) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    connection.putrequest("POST", "/recognize-stream")
    if length is not None:
        connection.putheader("Content-Length", length)  # '²' passes str.isdigit() (B1)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 400 and b"Content-Length is required" in response.read()
    connection.close()


def test_an_embedder_failure_is_500_and_the_server_keeps_serving(
    server: SpeakerServer, embed: FakeEmbed, caplog: pytest.LogCaptureFixture
) -> None:
    embed.error = RuntimeError("torch broke")
    assert request(server) == (500, {"error": "recognition failed"})
    assert "recognition failed: RuntimeError('torch broke')" in caplog.text
    embed.error = None
    assert request(server)[0] == 200


def test_warm_up_rejects_a_bad_embedding() -> None:
    assert service.warm_up(lambda samples: np.ones(3)).shape == (3,)
    with pytest.raises(RuntimeError, match="warm-up: bad embedding"):
        service.warm_up(lambda samples: np.array([np.nan, 1.0]))
    with pytest.raises(RuntimeError, match="warm-up: bad embedding"):
        service.warm_up(lambda samples: np.array([]))


def test_main_refuses_a_missing_profiles_dir(tmp_path: Path) -> None:
    args = ["--host", "127.0.0.1", "--port", "0", "--profiles-dir", str(tmp_path / "none")]
    with pytest.raises(RuntimeError, match="is not a directory"):
        service.main(args, lambda: lambda samples: np.ones(3))


def test_main_fails_before_listening_if_the_model_fails(tmp_path: Path) -> None:
    def load() -> service.Embed:
        raise OSError("checkpoint missing")

    args = ["--host", "127.0.0.1", "--port", "0", "--profiles-dir", str(tmp_path)]
    with pytest.raises(OSError, match="checkpoint missing"):
        service.main(args, load)
