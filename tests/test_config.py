import re
from pathlib import Path

import pytest

from piotr_voice_hub.config import ConfigError, HubConfig, load_config

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config" / "hub.example.yaml"

MINIMAL = """\
log_level: INFO
api: {host: 0.0.0.0, port: 8750}
playback_http: {port: 8751}
satellites:
  defaults: {end_silence_seconds: 0.8, max_segment_seconds: 15,
             reply_timeout_seconds: 30, follow_up_seconds: 8}
  devices:
    - {id: sat-a, area: bedroom, address: a.local, api_key: "key-a"}
    - {id: sat-b, area: kitchen, address: b.local, api_key: "key-b", follow_up_seconds: 5}
wake_phrase: Ryszardzie
stt: {model: large-v3-turbo, device: cuda, compute_type: float16, beam_size: 5,
      partial: {interval_seconds: 0.75, window_seconds: 4.0, beam_size: 1}}
speaker_recognition:
  url: http://127.0.0.1:2140
  timeout_seconds: 1.0
  profiles: {Maciek: /profiles/maciek.npz}
tts: {uri: tcp://127.0.0.1:10200, voice: pl_PL-bass-high, volume: 1.0}
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hub.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def load_text(tmp_path: Path, text: str, overrides: dict[str, object] | None = None) -> HubConfig:
    return load_config(write(tmp_path, text), overrides)


def expect_error(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_text(tmp_path, text)


def test_example_config_matches_design() -> None:
    config = load_config(EXAMPLE)
    assert config.log_level == "INFO"
    assert (config.api.host, config.api.port) == ("0.0.0.0", 8750)
    assert config.playback_http.port == 8751
    assert [s.id for s in config.satellites] == [
        "voice-pe-bedroom",
        "voice-pe-02",
        "voice-pe-03",
        "box3-office",
    ]
    assert config.wake_phrase == "Ryszardzie"
    assert config.stt.model == "large-v3-turbo"
    assert config.stt.partial.interval_seconds == 0.75
    assert config.speaker_recognition.profiles == {
        "Maciek": "/profiles/maciek/profile/speaker_profile.npz"
    }
    assert config.tts.voice == "pl_PL-bass-high"


def test_defaults_apply_and_device_values_win(tmp_path: Path) -> None:
    a, b = load_text(tmp_path, MINIMAL).satellites
    assert (a.end_silence_seconds, a.max_segment_seconds) == (0.8, 15.0)
    assert (a.reply_timeout_seconds, a.follow_up_seconds) == (30.0, 8.0)
    assert b.follow_up_seconds == 5.0
    assert b.reply_timeout_seconds == 30.0


def test_timing_value_may_come_from_device_only(tmp_path: Path) -> None:
    text = MINIMAL.replace(", follow_up_seconds: 8}", "}").replace(
        'api_key: "key-a"}', 'api_key: "key-a", follow_up_seconds: 7}'
    )
    a, b = load_text(tmp_path, text).satellites
    assert (a.follow_up_seconds, b.follow_up_seconds) == (7.0, 5.0)


def test_missing_timing_value_names_device(tmp_path: Path) -> None:
    text = MINIMAL.replace(", follow_up_seconds: 8}", "}")
    expect_error(tmp_path, text, r"satellites\.devices\[0\]: missing 'follow_up_seconds'")


def test_cli_overrides_win_over_file(tmp_path: Path) -> None:
    config = load_text(
        tmp_path,
        MINIMAL,
        {
            "log_level": "DEBUG",
            "api.host": "127.0.0.1",
            "api.port": 9000,
            "playback_http.port": 9001,
        },
    )
    assert config.log_level == "DEBUG"
    assert (config.api.host, config.api.port) == ("127.0.0.1", 9000)
    assert config.playback_http.port == 9001


def test_file_value_used_when_no_override(tmp_path: Path) -> None:
    config = load_text(tmp_path, MINIMAL, {"api.port": 9000})
    assert config.api.host == "0.0.0.0"
    assert config.playback_http.port == 8751


def test_override_is_validated(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"command-line overrides.*api\.port: must be between"):
        load_text(tmp_path, MINIMAL, {"api.port": 0})


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("log_level: INFO", "log_level: LOUD", "log_level: must be one of"),
        (
            "log_level: INFO\n",
            "log_level: INFO\nextra: 1\n",
            r"<top level>: unknown key\(s\): extra",
        ),
        ("wake_phrase: Ryszardzie\n", "", r"missing key\(s\): wake_phrase"),
        ("port: 8750", "port: '8750'", r"api\.port: must be an integer"),
        ("port: 8750", "port: true", r"api\.port: must be an integer"),
        ("port: 8751", "port: 70000", r"playback_http\.port: must be between 1 and 65535"),
        ("beam_size: 1", "beam_size: 0", r"stt\.partial\.beam_size: must be >= 1"),
        ("volume: 1.0", "volume: 1.5", r"tts\.volume: must be between 0\.0 and 1\.0"),
        ("timeout_seconds: 1.0", "timeout_seconds: 0", r"timeout_seconds: must be > 0"),
        ("voice: pl_PL-bass-high", "voice: ''", r"tts\.voice: must be a non-empty string"),
        ("id: sat-b", "id: sat-a", r"duplicate id\(s\): sat-a"),
        ("address: b.local", "address: a.local", r"duplicate address\(s\): a\.local"),
        (
            "follow_up_seconds: 5}",
            "follow_up_seconds: .nan}",
            r"devices\[1\]\.follow_up_seconds: must be a finite",
        ),
        (
            "reply_timeout_seconds: 30",
            "reply_timeout_seconds: .inf",
            r"satellites\.defaults\.reply_timeout_seconds: must be a finite",
        ),
        (
            "timeout_seconds: 1.0",
            "timeout_seconds: .nan",
            r"speaker_recognition\.timeout_seconds: must be a finite",
        ),
        ("volume: 1.0", "volume: .nan", r"tts\.volume: must be a finite"),
        (
            "uri: tcp://127.0.0.1:10200",
            "uri: http://127.0.0.1:10200",
            r"tts\.uri: .*tcp://host:port",
        ),
        ("uri: tcp://127.0.0.1:10200", "uri: tcp://127.0.0.1", r"tts\.uri: .*tcp://host:port"),
        ("uri: tcp://127.0.0.1:10200", "uri: tcp://127.0.0.1:x", r"tts\.uri: .*Port could not"),
        ("uri: tcp://127.0.0.1:10200", "uri: 'tcp://h:1?x=1'", r"tts\.uri: .*tcp://host:port"),
        ("uri: tcp://127.0.0.1:10200", "uri: 'tcp://h:1?'", r"tts\.uri: .*tcp://host:port"),
        ("uri: tcp://127.0.0.1:10200", "uri: 'tcp://h:1#f'", r"tts\.uri: .*tcp://host:port"),
        (
            "uri: tcp://127.0.0.1:10200",
            "uri: 'tcp://u:secret@h:1'",
            r"hub\.yaml: tts\.uri: expected tcp://host:port, without user info$",
        ),
        ("uri: tcp://127.0.0.1:10200", "uri: tcp://:10200", r"tts\.uri: .*tcp://host:port"),
        (
            "end_silence_seconds: 0.8",
            "end_silence_seconds: -1",
            r"satellites\.defaults\.end_silence_seconds: must be > 0",
        ),
        ('api_key: "key-a"}', 'api_key: "key-a", color: red}', r"devices\[0\]: unknown key"),
        ("max_segment_seconds: 15", "max_segment_secs: 15", r"satellites\.defaults: unknown key"),
        ("partial: {", "partial: {extra: 1, ", r"stt\.partial: unknown key\(s\): extra"),
        ("api: {host: 0.0.0.0, port: 8750}", "api: [1, 2]", "api: must be a mapping"),
    ],
)
def test_invalid_values(tmp_path: Path, old: str, new: str, message: str) -> None:
    assert old in MINIMAL
    expect_error(tmp_path, MINIMAL.replace(old, new, 1), message)


def test_empty_device_list(tmp_path: Path) -> None:
    start = MINIMAL.index("  devices:")
    end = MINIMAL.index("wake_phrase:")
    text = MINIMAL[:start] + "  devices: []\n" + MINIMAL[end:]
    expect_error(tmp_path, text, r"satellites\.devices: must be a non-empty list")


def test_invalid_yaml_syntax(tmp_path: Path) -> None:
    expect_error(tmp_path, "api: {host: [unclosed\n", "invalid YAML")


def test_duplicate_yaml_key(tmp_path: Path) -> None:
    expect_error(tmp_path, MINIMAL + "log_level: DEBUG\n", "duplicate key 'log_level'")


@pytest.mark.parametrize("text", ["", "- just\n- a list\n"])
def test_top_level_must_be_mapping(tmp_path: Path, text: str) -> None:
    expect_error(tmp_path, text, "<top level>: must be a mapping")


def test_redacted_hides_api_keys(tmp_path: Path) -> None:
    config = load_text(tmp_path, MINIMAL)
    assert "key-a" in repr(config)
    assert "key-a" not in repr(config.redacted())
    assert config.redacted().satellites[0].id == "sat-a"


def test_errors_name_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "")
    with pytest.raises(
        ConfigError, match=f"^{re.escape(str(path))}: <top level>: must be a mapping"
    ):
        load_config(path)


def test_override_into_non_mapping_section_names_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, MINIMAL.replace("api: {host: 0.0.0.0, port: 8750}", "api: 5"))
    with pytest.raises(
        ConfigError, match=f"^{re.escape(str(path))} \\(with command-line overrides\\): api"
    ):
        load_config(path, {"api.port": 9000})


def test_non_utf8_file_is_a_one_line_error(tmp_path: Path) -> None:
    path = tmp_path / "hub.yaml"
    path.write_bytes(b"log_level: \xff\xfe\n")
    with pytest.raises(ConfigError, match="not valid UTF-8") as error:
        load_config(path)
    assert "\n" not in str(error.value)


def test_unhashable_yaml_key_is_a_one_line_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid YAML.*unhashable key") as error:
        load_text(tmp_path, "? [a, b]\n: 1\n")
    assert "\n" not in str(error.value)


def test_yaml_syntax_error_is_one_line(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid YAML") as error:
        load_text(tmp_path, "api: {host: [unclosed\n")
    assert "\n" not in str(error.value)
