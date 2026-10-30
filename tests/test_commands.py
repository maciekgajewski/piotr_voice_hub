"""T-017, T-019, T-020, T-021: parsing, validating and routing the API commands `speak` (with
`listen_after`), `play`, `release` and `stop`."""

import json
import logging
from typing import Any

import pytest

from piotr_voice_hub.hub.commands import CommandError, CommandRouter, ErrorCode


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.refuse: CommandError | None = None

    def speak(self, text: str, volume: float | None, listen_after: bool) -> str:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("speak", text, volume, listen_after))
        return "playback-1"

    def play(self, url: str, listen_after: bool) -> str:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("play", url, listen_after))
        return "playback-2"

    def release(self) -> None:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("release",))

    def stop(self) -> None:
        if self.refuse is not None:
            raise self.refuse
        self.calls.append(("stop",))


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
    # The satellite's volume stays; no follow-up window unless asked.
    assert session.calls == [("speak", "Jest piąta.", None, False)]


@pytest.mark.parametrize("volume", [0, 0.25, 1])
def test_speak_takes_a_volume_from_0_to_1(volume: float) -> None:
    commands, session = router()
    data = {"text": "Jest piąta.", "volume": volume, "listen_after": False}
    assert commands.handle(command(data=data))["type"] == "ack"
    assert session.calls == [("speak", "Jest piąta.", float(volume), False)]


def test_speak_with_listen_after_asks_for_the_follow_up_window() -> None:
    commands, session = router()
    reply = commands.handle(command(data={"text": "Wziąć parasol?", "listen_after": True}))
    assert reply["type"] == "ack" and reply["data"] == {"playback_id": "playback-1"}
    assert session.calls == [("speak", "Wziąć parasol?", None, True)]


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.2:8000/dzwonek.mp3",
        "https://example.com/a.flac?token=x",
        "HTTP://[fd00::2]/a.wav",
    ],
)
def test_play_is_acked_with_its_playback_id_and_passes_the_url_unchanged(url: str) -> None:
    commands, session = router()
    reply = commands.handle(command("play", data={"url": url}))
    assert reply == {
        "type": "ack",
        "reply_to": "c7",
        "satellite": "voice-pe-02",
        "data": {"playback_id": "playback-2"},
    }
    assert commands.handle(command("play", data={"url": url, "listen_after": True}))["type"] == (
        "ack"
    )
    assert session.calls == [("play", url, False), ("play", url, True)]


@pytest.mark.parametrize("kind", ["release", "stop"])
def test_release_and_stop_are_acked_with_empty_data(kind: str) -> None:
    commands, session = router()
    assert commands.handle(command(kind)) == {
        "type": "ack",
        "reply_to": "c7",
        "satellite": "voice-pe-02",
        "data": {},
    }
    assert commands.handle(command(kind, data={}))["type"] == "ack"
    assert session.calls == [(kind,), (kind,)]


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
    pytest.param(command("play"), id="play-no-url"),
    pytest.param(command("play", data={"media_url": "http://h/a.mp3"}), id="play-unknown-key"),
    pytest.param(command("play", data={"url": "http://h/a.mp3", "volume": 0.5}), id="play-volume"),
    pytest.param(command("play", data={"url": 5}), id="play-url-not-a-string"),
    pytest.param(command("play", data={"url": ""}), id="play-url-empty"),
    pytest.param(command("play", data={"url": "http://h/" + "a" * 2048}), id="play-url-too-long"),
    pytest.param(command("play", data={"url": "file:///tmp/a.mp3"}), id="play-file-url"),
    pytest.param(command("play", data={"url": "ftp://h/a.mp3"}), id="play-ftp-url"),
    pytest.param(command("play", data={"url": "rtsp://h/a"}), id="play-rtsp-url"),
    pytest.param(command("play", data={"url": "a.mp3"}), id="play-relative-url"),
    pytest.param(command("play", data={"url": "http:///a.mp3"}), id="play-url-no-host"),
    pytest.param(command("play", data={"url": "http://h:99999/a"}), id="play-url-bad-port"),
    pytest.param(command("play", data={"url": "http://[::1/a"}), id="play-url-bad-ipv6"),
    pytest.param(command("play", data={"url": "http://h/a b.mp3"}), id="play-url-space"),
    pytest.param(command("play", data={"url": "http://h/a\n.mp3"}), id="play-url-newline"),
    pytest.param(
        command("play", data={"url": "http://h/a.mp3", "listen_after": "yes"}),
        id="play-listen-after-a-string",
    ),
    pytest.param(command("release", data={"x": 1}), id="release-with-data"),
    pytest.param(command("stop", data={"playback_id": "x"}), id="stop-with-data"),
    pytest.param(command(data={}), id="speak-no-text"),
    pytest.param(command(data={"text": 5}), id="speak-text-not-a-string"),
    pytest.param(command(data={"text": "   "}), id="speak-empty-text"),
    pytest.param(command(data={"text": "a" * 1001}), id="speak-text-over-the-cap"),
    pytest.param(command(data={"text": "x", "voice": "y"}), id="speak-unknown-key"),
    pytest.param(command(data={"text": "x", "listen_after": 1}), id="listen-after-not-a-bool"),
    pytest.param(command(data={"text": "x", "listen_after": "true"}), id="listen-after-a-string"),
    pytest.param(command(data={"text": "x", "listen_after": None}), id="listen-after-null"),
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
    assert error(commands.handle(command("stop")))[2] == code.value
    play = command("play", data={"url": "http://h/a.mp3"})
    assert error(commands.handle(play))[2] == code.value


def test_one_info_line_per_command_without_the_text(caplog: pytest.LogCaptureFixture) -> None:
    commands, _ = router()
    caplog.set_level(logging.INFO, "piotr_voice_hub.hub.commands")
    commands.handle(command(data={"text": "Tajny tekst"}))
    commands.handle(command("play", data={"url": "http://h/a.mp3?token=Tajny"}))
    commands.handle(command("play"))
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "API command speak c7 for voice-pe-02: ack {'playback_id': 'playback-1'} (11 characters)",
        "API command play c7 for voice-pe-02: ack {'playback_id': 'playback-2'}",
        "API command play c7 for voice-pe-02: error bad_request (play needs a string 'url' of 1 "
        "to 2048 characters)",
    ]
    assert not any("Tajny" in line for line in lines)
