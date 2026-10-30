"""API commands `speak`, `release` (design.md §6; T-017), `stop` (T-019) and `play` (T-021).

A client message is `{"type", "id", "satellite", "data"}`. `CommandRouter.handle` validates it,
calls the satellite's session and returns the reply for that client: `ack` (with the command's
result) or `error` (`{"code", "message"}`), both with `reply_to` and no `ts` (design.md's
example). It never raises for a client's mistake. `speak` takes `listen_after` (T-020). `play`
takes a `url` (`http` or `https`, with a host; the satellite fetches it itself, so the hub
never does) and `listen_after`; no volume (design.md §6 gives it none).
"""

import json
import logging
import urllib.parse
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from piotr_voice_hub.tts.interfaces import TtsTextError
from piotr_voice_hub.tts.wyoming_piper import check_text

COMMAND_KEYS = {"type", "id", "satellite", "data"}
SPEAK_KEYS = {"text", "listen_after", "volume"}
PLAY_KEYS = {"url", "listen_after"}
URL_SCHEMES = ("http", "https")
MAX_URL_CHARS = 2048
MAX_ID_CHARS = 128
NO_DATA = ("release", "stop")

_logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    BUSY = "busy"
    UNKNOWN_SATELLITE = "unknown_satellite"
    SATELLITE_OFFLINE = "satellite_offline"
    BAD_REQUEST = "bad_request"


class CommandError(Exception):
    """A refused command: the `error` reply's code and message."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Speaker(Protocol):
    """What a command needs from a satellite's session. Each raises CommandError (busy,
    satellite_offline) when the session cannot take the command now."""

    def speak(self, text: str, volume: float | None, listen_after: bool) -> str: ...  # its id

    def play(self, url: str, listen_after: bool) -> str: ...  # its id

    def release(self) -> None: ...

    def stop(self) -> None: ...


def ack(reply_to: str, satellite: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "ack", "reply_to": reply_to, "satellite": satellite, "data": data}


def error(reply_to: str | None, satellite: str | None, failure: CommandError) -> dict[str, Any]:
    return {
        "type": "error",
        "reply_to": reply_to,
        "satellite": satellite,
        "data": {"code": failure.code.value, "message": failure.message},
    }


class CommandRouter:
    """Routes commands to the satellites' sessions, by satellite id."""

    def __init__(self, sessions: Mapping[str, Speaker]) -> None:
        self._sessions = dict(sessions)

    def handle(self, message: str | bytes) -> dict[str, Any]:
        """The reply to one client message."""
        reply_to: str | None = None
        satellite: str | None = None
        kind = "?"
        try:
            command = _load(message)
            # Echoed in an error too, when they are valid, so the client can match it.
            reply_to, satellite = (
                _valid_str(command.get("id")),
                _valid_str(command.get("satellite")),
            )
            _validate(command)
            kind = command["type"]
            reply = ack(command["id"], command["satellite"], self._run(command))
            _logger.info(
                "API command %s %s for %s: ack %s%s",
                kind,
                reply_to,
                satellite,
                reply["data"],
                _text_note(command),
            )
            return reply
        except CommandError as failure:
            _logger.info(
                "API command %s %s for %s: error %s (%s)",
                kind,
                reply_to,
                satellite,
                failure.code.value,
                failure.message,
            )
            return error(reply_to, satellite, failure)

    def _run(self, command: dict[str, Any]) -> dict[str, Any]:
        kind, data = command["type"], command["data"]
        if kind not in ("speak", "play", *NO_DATA):
            raise CommandError(ErrorCode.BAD_REQUEST, f"unknown command type {kind!r}")
        session = self._sessions.get(command["satellite"])
        if session is None:
            raise CommandError(
                ErrorCode.UNKNOWN_SATELLITE, f"no satellite {command['satellite']!r}"
            )
        if kind in NO_DATA:
            if data:
                raise CommandError(ErrorCode.BAD_REQUEST, f"{kind} takes no data")
            if kind == "release":
                session.release()
            else:
                session.stop()
            return {}
        if kind == "play":
            url, listen_after = _play_args(data)
            return {"playback_id": session.play(url, listen_after)}
        text, volume, listen_after = self._speak_args(data)
        return {"playback_id": session.speak(text, volume, listen_after)}

    def _speak_args(self, data: dict[str, Any]) -> tuple[str, float | None, bool]:
        """The text; the volume: None (the satellite's volume is not touched) unless the
        command passes one (T-017 follow-up); `listen_after` (default false, T-020)."""
        if unknown := set(data) - SPEAK_KEYS:
            raise CommandError(ErrorCode.BAD_REQUEST, f"unknown speak keys {sorted(unknown)}")
        text = data.get("text")
        if not isinstance(text, str):
            raise CommandError(ErrorCode.BAD_REQUEST, "speak needs a string 'text'")
        try:
            text = check_text(text)
        except TtsTextError as failure:
            raise CommandError(ErrorCode.BAD_REQUEST, str(failure)) from None
        listen_after = _listen_after(data)
        if "volume" not in data:
            return text, None, listen_after
        volume = data["volume"]
        # No math.isfinite: it overflows on a huge int. NaN and infinities fail the range.
        if isinstance(volume, bool) or not isinstance(volume, int | float) or not 0 <= volume <= 1:
            raise CommandError(ErrorCode.BAD_REQUEST, "'volume' must be a number from 0 to 1")
        return text, float(volume), listen_after


def _play_args(data: dict[str, Any]) -> tuple[str, bool]:
    """The URL (checked, never fetched) and `listen_after` (default false)."""
    if unknown := set(data) - PLAY_KEYS:
        raise CommandError(ErrorCode.BAD_REQUEST, f"unknown play keys {sorted(unknown)}")
    url = data.get("url")
    if not isinstance(url, str) or not 0 < len(url) <= MAX_URL_CHARS:
        raise CommandError(
            ErrorCode.BAD_REQUEST, f"play needs a string 'url' of 1 to {MAX_URL_CHARS} characters"
        )
    if not url.isprintable() or " " in url:
        raise CommandError(ErrorCode.BAD_REQUEST, "'url' must not contain spaces or controls")
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        _ = parts.port  # ValueError for a bad port
    except ValueError as failure:
        raise CommandError(ErrorCode.BAD_REQUEST, f"'url' is not a URL: {failure}") from None
    if parts.scheme not in URL_SCHEMES or not host:
        raise CommandError(ErrorCode.BAD_REQUEST, "'url' must be an http or https URL with a host")
    return url, _listen_after(data)


def _listen_after(data: dict[str, Any]) -> bool:
    listen_after = data.get("listen_after", False)
    if not isinstance(listen_after, bool):
        raise CommandError(ErrorCode.BAD_REQUEST, "'listen_after' must be a boolean")
    return listen_after


def _valid_str(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS else None


def _load(message: str | bytes) -> dict[str, Any]:
    if not isinstance(message, str):
        raise CommandError(ErrorCode.BAD_REQUEST, "commands are JSON text messages, not binary")
    try:
        command = json.loads(message)
    except json.JSONDecodeError as failure:
        raise CommandError(ErrorCode.BAD_REQUEST, f"not JSON: {failure}") from None
    except (ValueError, RecursionError) as failure:
        # Valid JSON that json refuses: an integer over 4300 digits (ValueError) or deep
        # nesting (RecursionError). Still one bad_request reply (PR #21 review S1).
        raise CommandError(ErrorCode.BAD_REQUEST, f"not JSON: {type(failure).__name__}") from None
    if not isinstance(command, dict):
        raise CommandError(ErrorCode.BAD_REQUEST, "a command is a JSON object")
    return command


def _validate(command: dict[str, Any]) -> None:
    command.setdefault("data", {})
    if unknown := set(command) - COMMAND_KEYS:
        raise CommandError(ErrorCode.BAD_REQUEST, f"unknown keys {sorted(unknown)}")
    for key in ("type", "id", "satellite"):
        if _valid_str(command.get(key)) is None:
            raise CommandError(
                ErrorCode.BAD_REQUEST, f"{key!r} must be a string of 1 to {MAX_ID_CHARS} characters"
            )
    if not isinstance(command["data"], dict):
        raise CommandError(ErrorCode.BAD_REQUEST, "'data' must be an object")


def _text_note(command: dict[str, Any]) -> str:
    text = command["data"].get("text")
    return f" ({len(text.strip())} characters)" if isinstance(text, str) else ""
