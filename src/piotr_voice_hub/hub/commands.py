"""API commands `speak` and `release` (design.md §6; T-017).

A client message is `{"type", "id", "satellite", "data"}`. `CommandRouter.handle` validates it,
calls the satellite's session and returns the reply for that client: `ack` (with the command's
result) or `error` (`{"code", "message"}`), both with `reply_to` and no `ts` (design.md's
example). It never raises for a client's mistake. `play`, `stop` and `listen_after` are later
tasks (T-019, T-020, T-021) and are refused as not supported yet.
"""

import json
import logging
import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Protocol

from piotr_voice_hub.tts.interfaces import TtsTextError
from piotr_voice_hub.tts.wyoming_piper import check_text

COMMAND_KEYS = {"type", "id", "satellite", "data"}
SPEAK_KEYS = {"text", "listen_after", "volume"}
MAX_ID_CHARS = 128
NOT_YET = {"play": "T-021", "stop": "T-019"}

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
    """What a command needs from a satellite's session. Both raise CommandError (busy,
    satellite_offline) when the session cannot take the command now."""

    def speak(self, text: str, volume: float) -> str: ...  # the playback_id

    def release(self) -> None: ...


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

    def __init__(self, sessions: Mapping[str, Speaker], default_volume: float) -> None:
        self._sessions = dict(sessions)
        self._default_volume = default_volume

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
        if kind in NOT_YET:
            raise CommandError(
                ErrorCode.BAD_REQUEST, f"{kind!r} is not supported yet ({NOT_YET[kind]})"
            )
        if kind not in ("speak", "release"):
            raise CommandError(ErrorCode.BAD_REQUEST, f"unknown command type {kind!r}")
        session = self._sessions.get(command["satellite"])
        if session is None:
            raise CommandError(
                ErrorCode.UNKNOWN_SATELLITE, f"no satellite {command['satellite']!r}"
            )
        if kind == "release":
            if data:
                raise CommandError(ErrorCode.BAD_REQUEST, "release takes no data")
            session.release()
            return {}
        text, volume = self._speak_args(data)
        return {"playback_id": session.speak(text, volume)}

    def _speak_args(self, data: dict[str, Any]) -> tuple[str, float]:
        if unknown := set(data) - SPEAK_KEYS:
            raise CommandError(ErrorCode.BAD_REQUEST, f"unknown speak keys {sorted(unknown)}")
        text = data.get("text")
        if not isinstance(text, str):
            raise CommandError(ErrorCode.BAD_REQUEST, "speak needs a string 'text'")
        try:
            text = check_text(text)
        except TtsTextError as failure:
            raise CommandError(ErrorCode.BAD_REQUEST, str(failure)) from None
        listen_after = data.get("listen_after", False)
        if not isinstance(listen_after, bool):
            raise CommandError(ErrorCode.BAD_REQUEST, "'listen_after' must be a boolean")
        if listen_after:
            raise CommandError(ErrorCode.BAD_REQUEST, "'listen_after' is not supported yet (T-020)")
        volume = data.get("volume", self._default_volume)
        if (
            isinstance(volume, bool)
            or not isinstance(volume, int | float)
            or not math.isfinite(volume)
            or not 0.0 <= volume <= 1.0
        ):
            raise CommandError(ErrorCode.BAD_REQUEST, "'volume' must be a number from 0 to 1")
        return text, float(volume)


def _valid_str(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID_CHARS else None


def _load(message: str | bytes) -> dict[str, Any]:
    if not isinstance(message, str):
        raise CommandError(ErrorCode.BAD_REQUEST, "commands are JSON text messages, not binary")
    try:
        command = json.loads(message)
    except json.JSONDecodeError as failure:
        raise CommandError(ErrorCode.BAD_REQUEST, f"not JSON: {failure}") from None
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
