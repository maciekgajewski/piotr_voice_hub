"""T-017: parsing, validating and routing the API commands `speak` and `release`."""

import json
import logging

import pytest

from piotr_voice_hub.hub.commands import CommandError, CommandRouter, ErrorCode


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...] | tuple[str, str, float | None]] = []
        self.refuse: CommandError | None = None

    def speak(self, text: str, volume: float | None) -> str:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("speak", text, volume))
        return "playback-1"

    def release(self) -> None:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("release",))


def router() -> tuple[CommandRouter, FakeSession]:
    session = FakeSession()
    return CommandRouter({"voice-pe-02": session}), session


def command(kind: str = "speak", data: object = None, **fields: object) -> str:
    message: dict[str, object] = {"type": kind, "id": "c7", "satellite": "voice-pe-02"}
    if data is not None:
        message["data"] = data
    message.update(fields)
    return json.dumps(message)


def test_speak_is_acked_with_its_playback_id_and_no_volume_unless_given() -> None:
    commands, session = router()
    reply = commands.handle(command(data={"text": "  Jest piąta.  "}))
    assert reply == {
        "type": "ack",
        "reply_to": "c7",
        "satellite": "voice-pe-02",
        "data": {"playback_id": "playback-1"},
    }
    assert session.calls == [("speak", "Jest piąta.", None)]  # the satellite's volume stays


@pytest.mark.parametrize("volume", [0, 0.25, 1])
def test_speak_takes_a_volume_from_0_to_1(volume: float) -> None:
    commands, session = router()
    data = {"text": "Jest piąta.", "volume": volume, "listen_after": False}
    assert commands.handle(command(data=data))["type"] == "ack"
    assert session.calls == [("speak", "Jest piąta.", float(volume))]


def test_release_is_acked_with_empty_data() -> None:
    commands, session = router()
    assert commands.handle(command("release")) == {
        "type": "ack",
        "reply_to": "c7",
        "satellite": "voice-pe-02",
        "data": {},
    }
    assert commands.handle(command("release", data={}))["type"] == "ack"
    assert session.calls == [("release",), ("release",)]


def error(reply: dict[str, object]) -> tuple[object, object, str]:
    assert reply["type"] == "error"
    data = reply["data"]
    assert isinstance(data, dict) and isinstance(data["message"], str) and data["message"]
    return reply["reply_to"], reply["satellite"], data["code"]


BAD = [
    pytest.param(b"{}", id="binary"),
    pytest.param("not json", id="not-json"),
    pytest.param("[1]", id="not-an-object"),
    pytest.param(json.dumps({"id": "c7", "satellite": "voice-pe-02"}), id="no-type"),
    pytest.param(command(extra=1), id="unknown-key"),
    pytest.param(command(data=[]), id="data-not-an-object"),
    pytest.param(command("hello"), id="unknown-type"),
    pytest.param(command("play", data={"media_url": "x"}), id="play-not-yet"),
    pytest.param(command("stop"), id="stop-not-yet"),
    pytest.param(command("release", data={"x": 1}), id="release-with-data"),
    pytest.param(command(data={}), id="speak-no-text"),
    pytest.param(command(data={"text": 5}), id="speak-text-not-a-string"),
    pytest.param(command(data={"text": "   "}), id="speak-empty-text"),
    pytest.param(command(data={"text": "a" * 1001}), id="speak-text-over-the-cap"),
    pytest.param(command(data={"text": "x", "voice": "y"}), id="speak-unknown-key"),
    pytest.param(command(data={"text": "x", "listen_after": True}), id="listen-after-not-yet"),
    pytest.param(command(data={"text": "x", "listen_after": 1}), id="listen-after-not-a-bool"),
    pytest.param(command(data={"text": "x", "volume": 1.5}), id="volume-over-1"),
    pytest.param(command(data={"text": "x", "volume": -0.1}), id="volume-under-0"),
    pytest.param(command(data={"text": "x", "volume": True}), id="volume-bool"),
    pytest.param(command(data={"text": "x", "volume": "0.5"}), id="volume-string"),
    pytest.param(
        '{"type":"speak","id":"c7","satellite":"voice-pe-02","data":{"text":"x","volume":NaN}}',
        id="volume-nan",
    ),
    pytest.param(
        '{"type":"speak","id":"c7","satellite":"voice-pe-02",'
        '"data":{"text":"x","volume":Infinity}}',
        id="volume-infinity",
    ),
    pytest.param(command(data={"text": "x", "volume": 10**400}), id="volume-huge-int"),
    pytest.param(
        '{"type":"speak","id":"c7","satellite":"x","data":{"volume":' + "1" * 5000 + "}}",
        id="int-over-4300-digits",
    ),
    pytest.param("[" * 30000 + "]" * 30000, id="deep-nesting"),
]


@pytest.mark.parametrize("message", BAD)
def test_a_malformed_command_is_a_bad_request(message: str | bytes) -> None:
    commands, session = router()
    _, _, code = error(commands.handle(message))
    assert code == "bad_request" and session.calls == []


def test_an_error_echoes_the_id_and_satellite_when_they_are_valid() -> None:
    commands, _ = router()
    assert error(commands.handle(command("hello"))) == ("c7", "voice-pe-02", "bad_request")
    assert error(commands.handle(command("hello", id=7))) == (None, "voice-pe-02", "bad_request")
    assert error(commands.handle("nope")) == (None, None, "bad_request")


def test_an_unknown_satellite() -> None:
    commands, _ = router()
    reply = commands.handle(command(data={"text": "x"}, satellite="box3-office"))
    assert error(reply) == ("c7", "box3-office", "unknown_satellite")


@pytest.mark.parametrize("code", [ErrorCode.BUSY, ErrorCode.SATELLITE_OFFLINE])
def test_the_session_refusing_is_the_reply(code: ErrorCode) -> None:
    commands, session = router()
    session.refuse = CommandError(code, "voice-pe-02 is listening, not processing")
    assert error(commands.handle(command(data={"text": "x"}))) == ("c7", "voice-pe-02", code.value)
    assert error(commands.handle(command("release")))[2] == code.value


def test_one_info_line_per_command_without_the_text(caplog: pytest.LogCaptureFixture) -> None:
    commands, _ = router()
    caplog.set_level(logging.INFO, "piotr_voice_hub.hub.commands")
    commands.handle(command(data={"text": "Tajny tekst"}))
    commands.handle(command("stop"))
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "API command speak c7 for voice-pe-02: ack {'playback_id': 'playback-1'} (11 characters)",
        "API command stop c7 for voice-pe-02: error bad_request ('stop' is not supported yet "
        "(T-019))",
    ]
    assert not any("Tajny" in line for line in lines)
