"""The hub's speaker-recognition service (T-018, design.md §4.1 step 5, §9).

Runs inside its own image (docker/speaker.Dockerfile), not in the hub: the image copies this
file next to docker/speaker/entrypoint.py, which supplies the SpeechBrain ECAPA embedder. It
imports only the stdlib and numpy, so the hub's tests cover it with a fake embedder.

Ported from piotr_the_ai_server `ai_server/speaker_recognition/` @ a299643 (http_server.py,
profiles.py). The request and the answer of `/recognize-stream` are unchanged, and so is the
scoring: cosine similarity to each named profile's normalized embedding, the top one is the
`recognized_user` if its score reaches the threshold (0.45), and the confidence maps the score
to [0, 1] with 0.5 at the threshold. Changes:
- Stdlib `http.server`, one request at a time (the hub is the only client), instead of aiohttp.
  `/recognize` (WAV upload) and `/reload` are dropped: the hub sends its profiles with every
  request, so nothing is cached and a changed profile counts at the next request.
- Bounded: `Content-Length` is required and at most `MAX_AUDIO_SECONDS` of audio; a socket
  timeout per request.
- A profile path must lie under `--profiles-dir` (the read-only mount), else 400.
- The embedder warms up before the port opens, so healthy means warm.
- One INFO line per recognition (the top match, its score and the time), never the audio.
"""

import argparse
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from email.message import Message
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_THRESHOLD = 0.45  # the old service's
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_AUDIO_SECONDS = 60.0  # the hub sends at most max_segment_seconds (15 s), plus the wake word
MAX_BODY_BYTES = int(MAX_AUDIO_SECONDS * SAMPLE_RATE * SAMPLE_WIDTH)
MIN_AUDIO_SECONDS = 0.1
REQUEST_TIMEOUT_SECONDS = 10.0  # per socket read or write: a stalled client is dropped
PROFILE_KEY = "profile_embedding"  # the old profile builder's .npz key
WARMUP_SECONDS = 2.0

_logger = logging.getLogger("speaker_service")

type Embed = Callable[[np.ndarray], np.ndarray]  # float32 samples in [-1, 1) → embedding


class BadRequest(Exception):
    """The request is invalid: answered 400 with this message."""


def normalize(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    return vector / max(float(np.linalg.norm(vector)), 1.0e-12)


def load_profile(path: Path) -> np.ndarray:
    with np.load(path) as data:
        return normalize(data[PROFILE_KEY])


def score_to_confidence(score: float, threshold: float) -> float:
    """The old mapping: [0, threshold] → [0, 0.5], [threshold, 1] → [0.5, 1]."""
    if score <= threshold:
        return max(0.0, min(0.5, score / max(threshold, 1.0e-12) * 0.5))
    return max(0.5, min(1.0, 0.5 + (score - threshold) / max(1.0 - threshold, 1.0e-12) * 0.5))


def recognize(
    embedding: np.ndarray, profiles: Mapping[str, tuple[Path, np.ndarray]], threshold: float
) -> tuple[str, dict[str, Any]]:
    """The top profile's name, and the old answer: the top profile by cosine score, named
    (`recognized_user`) only at or above `threshold`."""
    assert profiles
    embedding = normalize(embedding)
    scored = sorted(
        (
            (float(np.dot(embedding, vector)), name, path)
            for name, (path, vector) in profiles.items()
        ),
        reverse=True,
    )
    score, name, path = scored[0]
    return name, {
        "recognized_user": name if score >= threshold else None,
        "confidence": score_to_confidence(score, threshold),
        "score": score,
        "threshold": threshold,
        "margin_to_second_best": score - scored[1][0] if len(scored) > 1 else None,
        "profile": str(path),
    }


def parse_profiles(header: str | None, root: Path) -> dict[str, Path]:
    """`X-Voice-Profiles` (JSON, name → path) → resolved paths, each under `root`. A relative
    path is relative to `root`."""
    try:
        payload = json.loads(header or "{}")
    except json.JSONDecodeError:
        raise BadRequest("X-Voice-Profiles must be valid JSON") from None
    if not isinstance(payload, dict) or not payload:
        raise BadRequest("X-Voice-Profiles must be a non-empty JSON object")
    profiles: dict[str, Path] = {}
    for name, value in payload.items():
        if not isinstance(name, str) or not name or not isinstance(value, str) or not value:
            raise BadRequest("X-Voice-Profiles must map user names to profile paths")
        path = (root / value).resolve()  # an absolute value replaces root
        if not path.is_relative_to(root):
            raise BadRequest(f"profile {name!r}: {value} is not under {root}")
        profiles[name] = path
    return profiles


def check_audio_format(headers: Message) -> None:
    for name, expected in (
        ("X-Audio-Sample-Rate", SAMPLE_RATE),
        ("X-Audio-Sample-Width", SAMPLE_WIDTH),
        ("X-Audio-Channels", CHANNELS),
    ):
        value = headers.get(name)
        if value != str(expected):
            raise BadRequest(f"{name} must be {expected}, got {value!r}")


def samples(pcm: bytes) -> np.ndarray:
    """16-bit little-endian PCM → float32 in [-1, 1), as the old embedder read its WAV."""
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


class SpeakerServer(HTTPServer):
    def __init__(
        self, address: tuple[str, int], embed: Embed, threshold: float, profiles_dir: Path
    ) -> None:
        self.embed = embed
        self.threshold = threshold
        self.profiles_dir = profiles_dir.resolve()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: SpeakerServer  # pyright: ignore[reportIncompatibleVariableOverride]
    timeout = REQUEST_TIMEOUT_SECONDS  # HTTP/1.0: one request per connection

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._reply(HTTPStatus.OK, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/recognize-stream":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            answer = self._recognize()
        except BadRequest as error:
            _logger.warning("bad request: %s", error)
            self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except (RuntimeError, ValueError) as error:  # torch reports its failures as these
            _logger.error("recognition failed: %r", error)
            self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "recognition failed"})
            return
        self._reply(HTTPStatus.OK, answer)

    def _recognize(self) -> dict[str, Any]:
        started = time.monotonic()
        server = self.server
        length = self.headers.get("Content-Length")
        if length is None or not (length.isascii() and length.isdigit()):  # not '²' (B1)
            raise BadRequest("Content-Length is required")
        if int(length) > MAX_BODY_BYTES:
            raise BadRequest(f"over {MAX_AUDIO_SECONDS:g} s of audio")
        # Read first, also for a request refused below: a reply over unread data would reset it.
        pcm = self.rfile.read(int(length))  # bounded by the socket timeout
        check_audio_format(self.headers)
        names = parse_profiles(self.headers.get("X-Voice-Profiles"), server.profiles_dir)
        if len(pcm) != int(length) or len(pcm) % SAMPLE_WIDTH:
            raise BadRequest(f"got {len(pcm)} of {length} bytes of 16-bit audio")
        audio_seconds = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)
        if audio_seconds < MIN_AUDIO_SECONDS:
            raise BadRequest(f"{audio_seconds:.3f} s of audio, at least {MIN_AUDIO_SECONDS:g} s")
        profiles: dict[str, tuple[Path, np.ndarray]] = {}
        for name, path in names.items():
            try:
                profiles[name] = (path, load_profile(path))
            except (OSError, ValueError, KeyError) as error:
                raise BadRequest(f"profile {name!r}: cannot load {path}: {error!r}") from None
        best_name, answer = recognize(server.embed(samples(pcm)), profiles, server.threshold)
        _logger.info(
            "%s: top %s score %.3f (threshold %.2f), %.1f s of audio, %.0f ms",
            answer["recognized_user"] or "no match",
            best_name,
            answer["score"],
            server.threshold,
            audio_seconds,
            (time.monotonic() - started) * 1000,
        )
        return answer

    def _reply(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        _logger.debug("%s %s", self.address_string(), format % args)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Speaker recognition (SpeechBrain ECAPA, CPU).")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--profiles-dir", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    return parser.parse_args(argv)


def warm_up(embed: Embed) -> np.ndarray:
    """One embedding of low noise before the port opens; it fails loudly on a broken model."""
    noise = np.random.default_rng(0).normal(0.0, 0.01, int(WARMUP_SECONDS * SAMPLE_RATE))
    embedding = np.asarray(embed(noise.astype(np.float32)))
    if embedding.ndim == 0 or embedding.size == 0 or not np.all(np.isfinite(embedding)):
        raise RuntimeError(f"warm-up: bad embedding (shape {embedding.shape})")
    return embedding


def main(argv: Sequence[str], load_embedder: Callable[[], Embed]) -> int:
    """Load the model, warm up, then serve until killed. Any startup failure raises (exit 1)."""
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    started = time.monotonic()
    embed = load_embedder()
    embedding = warm_up(embed)
    _logger.info(
        "model loaded and warm in %.1f s (embedding size %d)",
        time.monotonic() - started,
        embedding.size,
    )
    if not args.profiles_dir.is_dir():
        raise RuntimeError(f"--profiles-dir {args.profiles_dir} is not a directory")
    server = SpeakerServer((args.host, args.port), embed, args.threshold, args.profiles_dir)
    _logger.info(
        "listening on %s:%d, profiles under %s, threshold %.2f",
        args.host,
        args.port,
        server.profiles_dir,
        args.threshold,
    )
    with server:
        server.serve_forever()
    return 0
