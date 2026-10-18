"""Hub configuration (design.md §7): frozen dataclasses and a strict YAML loader.

Every key in §7 is required; there are no defaults in code. Satellite timing values come from
`satellites.defaults` and may be overridden per device. Unknown keys are errors.
"""

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
SATELLITE_TIMING_KEYS = (
    "end_silence_seconds",
    "max_segment_seconds",
    "reply_timeout_seconds",
    "follow_up_seconds",
)
# Dotted config paths that command-line options may override.
OVERRIDABLE_PATHS = ("log_level", "api.host", "api.port", "playback_http.port")
REDACTED = "***"


class ConfigError(Exception):
    """The config file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class ApiConfig:
    host: str
    port: int


@dataclass(frozen=True)
class PlaybackHttpConfig:
    port: int


@dataclass(frozen=True)
class SatelliteConfig:
    id: str
    area: str
    address: str
    api_key: str
    end_silence_seconds: float
    max_segment_seconds: float
    reply_timeout_seconds: float
    follow_up_seconds: float


@dataclass(frozen=True)
class SttPartialConfig:
    interval_seconds: float
    window_seconds: float
    beam_size: int


@dataclass(frozen=True)
class SttConfig:
    model: str
    device: str
    compute_type: str
    beam_size: int
    partial: SttPartialConfig


@dataclass(frozen=True)
class SpeakerRecognitionConfig:
    url: str
    timeout_seconds: float
    profiles: Mapping[str, str]


@dataclass(frozen=True)
class TtsConfig:
    uri: str
    voice: str
    volume: float


@dataclass(frozen=True)
class HubConfig:
    log_level: str
    api: ApiConfig
    playback_http: PlaybackHttpConfig
    satellites: tuple[SatelliteConfig, ...]
    wake_phrase: str
    stt: SttConfig
    speaker_recognition: SpeakerRecognitionConfig
    tts: TtsConfig

    def redacted(self) -> "HubConfig":
        """A copy safe to log: satellite API keys replaced."""
        satellites = tuple(dataclasses.replace(s, api_key=REDACTED) for s in self.satellites)
        return dataclasses.replace(self, satellites=satellites)


def load_config(path: Path, overrides: Mapping[str, object] | None = None) -> HubConfig:
    """Load and validate the config file; `overrides` (dotted path -> value) win over the file."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as error:
        raise ConfigError(f"cannot read config file {path}: {error}") from None
    except UnicodeDecodeError as error:
        raise ConfigError(f"config file {path} is not valid UTF-8: {error}") from None
    try:
        loaded = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {_one_line(error)}") from None
    try:
        raw = _mapping(loaded, "<top level>")
        for dotted, value in (overrides or {}).items():
            _apply_override(raw, dotted, value)
        return _parse_hub(raw)
    except ConfigError as error:
        source = f"{path} (with command-line overrides)" if overrides else str(path)
        raise ConfigError(f"{source}: {error}") from None


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError:
                raise ConstructorError(
                    None, None, f"unhashable key {key!r}", key_node.start_mark
                ) from None
            if key in seen:
                raise ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _apply_override(raw: dict[str, Any], dotted: str, value: object) -> None:
    assert dotted in OVERRIDABLE_PATHS, dotted
    *parents, leaf = dotted.split(".")
    section = raw
    for name in parents:
        child = section.setdefault(name, {})
        section = _mapping(child, name)
    section[leaf] = value


def _parse_hub(raw: dict[str, Any]) -> HubConfig:
    _keys(
        raw,
        "<top level>",
        {
            "log_level",
            "api",
            "playback_http",
            "satellites",
            "wake_phrase",
            "stt",
            "speaker_recognition",
            "tts",
        },
    )
    log_level = _str(raw, "log_level", "")
    if log_level not in LOG_LEVELS:
        raise ConfigError(f"log_level: must be one of {', '.join(LOG_LEVELS)}, got {log_level!r}")
    return HubConfig(
        log_level=log_level,
        api=_parse_api(_section(raw, "api", "")),
        playback_http=_parse_playback(_section(raw, "playback_http", "")),
        satellites=_parse_satellites(_section(raw, "satellites", "")),
        wake_phrase=_str(raw, "wake_phrase", ""),
        stt=_parse_stt(_section(raw, "stt", "")),
        speaker_recognition=_parse_speaker(_section(raw, "speaker_recognition", "")),
        tts=_parse_tts(_section(raw, "tts", "")),
    )


def _parse_api(raw: dict[str, Any]) -> ApiConfig:
    _keys(raw, "api", {"host", "port"})
    return ApiConfig(host=_str(raw, "host", "api"), port=_port(raw, "port", "api"))


def _parse_playback(raw: dict[str, Any]) -> PlaybackHttpConfig:
    _keys(raw, "playback_http", {"port"})
    return PlaybackHttpConfig(port=_port(raw, "port", "playback_http"))


def _parse_satellites(raw: dict[str, Any]) -> tuple[SatelliteConfig, ...]:
    _keys(raw, "satellites", {"defaults", "devices"})
    defaults = _section(raw, "defaults", "satellites")
    _keys(defaults, "satellites.defaults", set(), optional=set(SATELLITE_TIMING_KEYS))
    default_timing = {key: _positive(defaults, key, "satellites.defaults") for key in defaults}
    devices = raw["devices"]
    if not isinstance(devices, list) or not devices:
        raise ConfigError("satellites.devices: must be a non-empty list")
    satellites: list[SatelliteConfig] = []
    for index, device in enumerate(devices):
        path = f"satellites.devices[{index}]"
        device = _mapping(device, path)
        _keys(
            device,
            path,
            {"id", "area", "address", "api_key"},
            optional=set(SATELLITE_TIMING_KEYS),
        )
        timing: dict[str, float] = {}
        for key in SATELLITE_TIMING_KEYS:
            if key in device:
                timing[key] = _positive(device, key, path)
            elif key in default_timing:
                timing[key] = default_timing[key]
            else:
                raise ConfigError(
                    f"{path}: missing {key!r} (set it in satellites.defaults or on the device)"
                )
        satellites.append(
            SatelliteConfig(
                id=_str(device, "id", path),
                area=_str(device, "area", path),
                address=_str(device, "address", path),
                api_key=_str(device, "api_key", path),
                **timing,
            )
        )
    for field in ("id", "address"):
        values = [getattr(s, field) for s in satellites]
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            raise ConfigError(f"satellites.devices: duplicate {field}(s): {', '.join(duplicates)}")
    return tuple(satellites)


def _parse_stt(raw: dict[str, Any]) -> SttConfig:
    _keys(raw, "stt", {"model", "device", "compute_type", "beam_size", "partial"})
    partial = _section(raw, "partial", "stt")
    _keys(partial, "stt.partial", {"interval_seconds", "window_seconds", "beam_size"})
    return SttConfig(
        model=_str(raw, "model", "stt"),
        device=_str(raw, "device", "stt"),
        compute_type=_str(raw, "compute_type", "stt"),
        beam_size=_int(raw, "beam_size", "stt", minimum=1),
        partial=SttPartialConfig(
            interval_seconds=_positive(partial, "interval_seconds", "stt.partial"),
            window_seconds=_positive(partial, "window_seconds", "stt.partial"),
            beam_size=_int(partial, "beam_size", "stt.partial", minimum=1),
        ),
    )


def _parse_speaker(raw: dict[str, Any]) -> SpeakerRecognitionConfig:
    _keys(raw, "speaker_recognition", {"url", "timeout_seconds", "profiles"})
    profiles = _section(raw, "profiles", "speaker_recognition")
    return SpeakerRecognitionConfig(
        url=_str(raw, "url", "speaker_recognition"),
        timeout_seconds=_positive(raw, "timeout_seconds", "speaker_recognition"),
        profiles={name: _str(profiles, name, "speaker_recognition.profiles") for name in profiles},
    )


def _parse_tts(raw: dict[str, Any]) -> TtsConfig:
    _keys(raw, "tts", {"uri", "voice", "volume"})
    volume = _number(raw, "volume", "tts")
    if not 0.0 <= volume <= 1.0:
        raise ConfigError(f"tts.volume: must be between 0.0 and 1.0, got {volume}")
    return TtsConfig(uri=_str(raw, "uri", "tts"), voice=_str(raw, "voice", "tts"), volume=volume)


def _one_line(error: Exception) -> str:
    return " ".join(str(error).split())


def _where(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: must be a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise ConfigError(f"{path}: keys must be strings, got {key!r}")
    return value


def _section(raw: dict[str, Any], key: str, path: str) -> dict[str, Any]:
    return _mapping(raw[key], _where(path, key))


def _keys(
    raw: dict[str, Any], path: str, required: set[str], optional: set[str] | None = None
) -> None:
    allowed = required | (optional or set())
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{path}: unknown key(s): {', '.join(unknown)}")
    missing = sorted(required - set(raw))
    if missing:
        raise ConfigError(f"{path}: missing key(s): {', '.join(missing)}")


def _str(raw: dict[str, Any], key: str, path: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{_where(path, key)}: must be a non-empty string, got {value!r}")
    return value


def _int(raw: dict[str, Any], key: str, path: str, minimum: int, maximum: int | None = None) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{_where(path, key)}: must be an integer, got {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise ConfigError(f"{_where(path, key)}: must be {bounds}, got {value}")
    return value


def _port(raw: dict[str, Any], key: str, path: str) -> int:
    return _int(raw, key, path, minimum=1, maximum=65535)


def _number(raw: dict[str, Any], key: str, path: str) -> float:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{_where(path, key)}: must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ConfigError(f"{_where(path, key)}: must be a finite number, got {value}")
    return float(value)


def _positive(raw: dict[str, Any], key: str, path: str) -> float:
    value = _number(raw, key, path)
    if value <= 0:
        raise ConfigError(f"{_where(path, key)}: must be > 0, got {value}")
    return value
