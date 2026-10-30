"""Speaker-recognition HTTP client (T-018, design.md §4.1 step 5, §5).

The request is the old project's `/recognize-stream` (piotr_the_ai_server
ai_server/speaker_recognition/client.py @ a299643), so it works with our service
(`speaker/service.py`) and the old one alike: a POST of the utterance's PCM with
`X-Audio-Sample-Rate/Width/Channels` and `X-Voice-Profiles` (JSON, name → `.npz` path inside
the service), answered with JSON (`recognized_user`, `confidence`, `score`, `threshold`, ...).
Changes from the old client:
- One plain HTTP/1.1 request with `Content-Length` on `asyncio.open_connection` (no aiohttp;
  the old one streamed the audio during capture). `Connection: close`; the answer is read to
  EOF, at most `MAX_RESPONSE_BYTES`.
- The whole request (connect, send, answer) is bounded by `timeout_seconds` (design §7); the
  old session bounded only its wait for the result, by the same 1 s.
- It never raises for a service failure: the speaker is a hint. A refused connect, a timeout,
  an HTTP error or a malformed answer is one WARNING and None. Anything else that goes wrong in
  here is one WARNING and None too (PR #27 review B1: a malformed answer must not stop the
  hub); only a cancellation passes through.
"""

import asyncio
import json
import logging
import time
from collections.abc import Mapping

from piotr_voice_hub.config import SpeakerRecognitionConfig, parse_http_url
from piotr_voice_hub.speaker.interfaces import SpeakerMatch

PATH = "/recognize-stream"
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1
MAX_RESPONSE_BYTES = 64 * 1024  # the answer is a few hundred bytes of JSON

_logger = logging.getLogger(__name__)


class SpeakerRecognitionError(Exception):
    """The service answered with an error, or something that is not a valid answer."""


class HttpSpeakerRecognition:
    def __init__(self, config: SpeakerRecognitionConfig) -> None:
        self._host, self._port = parse_http_url(config.url)
        self._timeout = config.timeout_seconds
        self._profiles = dict(config.profiles)
        # ASCII JSON: header values must be latin-1; the service decodes the escapes.
        self._profiles_header = json.dumps(self._profiles, ensure_ascii=True)
        if self._profiles:
            _logger.info(
                "speaker hint: %s:%d, profiles %s, timeout %g s",
                self._host,
                self._port,
                ", ".join(sorted(self._profiles)),
                self._timeout,
            )
        else:
            _logger.warning("speaker hint off: no speaker_recognition.profiles configured")

    async def recognize(self, pcm: bytes, request_id: str) -> SpeakerMatch | None:
        if not self._profiles or not pcm:
            return None
        started = time.monotonic()
        audio_ms = len(pcm) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)
        _logger.debug("speaker %s: POST %d ms of audio", request_id, audio_ms)
        try:
            async with asyncio.timeout(self._timeout):
                payload = await self._post(pcm)
            match, details = parse_result(payload)
        except TimeoutError:
            _logger.warning(
                "speaker %s: no answer within %g s; hint is null", request_id, self._timeout
            )
            return None
        except (OSError, SpeakerRecognitionError) as error:
            _logger.warning("speaker %s: recognition failed: %s; hint is null", request_id, error)
            return None
        except Exception as error:  # never CancelledError (a BaseException): that passes
            _logger.warning(
                "speaker %s: recognition failed unexpectedly: %r; hint is null", request_id, error
            )
            return None
        _logger.info(
            "speaker %s: %s (%s, %d ms of audio, %.0f ms)",
            request_id,
            "null" if match is None else f"{match.name} confidence {match.confidence:.2f}",
            details,
            audio_ms,
            (time.monotonic() - started) * 1000,
        )
        return match

    async def _post(self, pcm: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            head = (
                f"POST {PATH} HTTP/1.1\r\n"
                f"Host: {self._host}:{self._port}\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Length: {len(pcm)}\r\n"
                "Connection: close\r\n"
                f"X-Audio-Sample-Rate: {SAMPLE_RATE}\r\n"
                f"X-Audio-Sample-Width: {SAMPLE_WIDTH}\r\n"
                f"X-Audio-Channels: {CHANNELS}\r\n"
                f"X-Voice-Profiles: {self._profiles_header}\r\n"
                "\r\n"
            )
            writer.write(head.encode("latin-1"))
            writer.write(pcm)
            await writer.drain()
            response = bytearray()
            while chunk := await reader.read(65536):
                response += chunk
                if len(response) > MAX_RESPONSE_BYTES:
                    raise SpeakerRecognitionError(f"answer over {MAX_RESPONSE_BYTES} bytes")
        finally:
            writer.close()
        return parse_response(bytes(response))


def is_ascii_digits(text: str) -> bool:
    """Only 0-9: `str.isdigit()` also takes e.g. '²', which `int()` refuses (PR #27 B1)."""
    return text.isascii() and text.isdigit()


def parse_response(response: bytes) -> bytes:
    """The body of a complete HTTP/1.x 200 response; SpeakerRecognitionError otherwise."""
    head, separator, body = response.partition(b"\r\n\r\n")
    if not separator:
        raise SpeakerRecognitionError(f"incomplete HTTP answer ({len(response)} bytes)")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not is_ascii_digits(parts[1]):
        raise SpeakerRecognitionError(f"not an HTTP answer: {status_line[:80]!r}")
    headers: dict[str, str] = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    if "chunked" in headers.get("transfer-encoding", "").lower():
        raise SpeakerRecognitionError("chunked answers are not supported")
    if (length := headers.get("content-length")) is not None:
        if not is_ascii_digits(length):
            raise SpeakerRecognitionError(f"bad Content-Length {length[:20]!r}")
        if len(body) < int(length):
            raise SpeakerRecognitionError(f"truncated answer (Content-Length {length[:20]!r})")
        body = body[: int(length)]
    if parts[1] != "200":
        text = body[:200].decode("utf-8", "replace").strip()
        raise SpeakerRecognitionError(f"HTTP {parts[1]}: {text}")
    return body


def parse_result(body: bytes) -> tuple[SpeakerMatch | None, str]:
    """The match (None below the threshold) and a short summary for the log."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError) as error:
        # ValueError: JSONDecodeError, UnicodeDecodeError, and an integer over the int string
        # limit (4300 digits). RecursionError: nesting too deep.
        raise SpeakerRecognitionError(f"answer is not JSON: {type(error).__name__}") from None
    if not isinstance(payload, Mapping):
        raise SpeakerRecognitionError("answer is not a JSON object")
    user = payload.get("recognized_user")
    if user is not None and (not isinstance(user, str) or not user):
        raise SpeakerRecognitionError(f"bad recognized_user {user!r}")
    numbers = {}
    for key in ("confidence", "score", "threshold"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SpeakerRecognitionError(f"bad {key} {str(value)[:20]!r}")
        try:
            numbers[key] = float(value)
        except OverflowError:  # an integer too large for a float
            raise SpeakerRecognitionError(f"bad {key}: too large") from None
    if not 0.0 <= numbers["confidence"] <= 1.0:
        raise SpeakerRecognitionError(f"confidence {numbers['confidence']} not in [0, 1]")
    details = f"score {numbers['score']:.3f}, threshold {numbers['threshold']:.3f}"
    if user is None:
        return None, details
    return SpeakerMatch(user, round(numbers["confidence"], 3)), details
