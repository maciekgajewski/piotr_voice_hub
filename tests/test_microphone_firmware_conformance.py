from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

FIRMWARE_ROOT = Path("firmware/esphome")
FIRMWARE_PACKAGES = (
    FIRMWARE_ROOT / "packages/home-assistant-voice-pe-piotr-standard.yaml",
    FIRMWARE_ROOT / "packages/esp32-s3-box-3-ryszardzie.yaml",
)
API_SERVICES = FIRMWARE_ROOT / "packages/piotr-voice-satellite-api-services.yaml"
DEVICE_YAML = (
    FIRMWARE_ROOT / "voice-pe-02.yaml",
    FIRMWARE_ROOT / "voice-pe-03.yaml",
    FIRMWARE_ROOT / "voice-pe-bedroom.yaml",
    FIRMWARE_ROOT / "box3-satellite.yaml",
)
CUE_ACTIONS = (
    "play_message_end_cue",
    "play_conversation_timeout_cue",
    "play_follow_up_ready_cue",
)
REQUIRED_ACTIONS = CUE_ACTIONS + (
    "start_open_mic_listening",
    "stop_listening",
    "reset_open_mic_wake_candidate",
    "set_visual_idle",
    "set_visual_listening",
    "set_visual_processing",
)


@pytest.mark.parametrize("package_path", FIRMWARE_PACKAGES, ids=lambda path: path.stem)
def test_reconnect_retains_error_until_explicit_server_visual_command(
    package_path: Path,
) -> None:
    """MP-VISUAL-001/003/005: connectivity alone never selects a connected visual."""
    source = package_path.read_text(encoding="utf-8")

    assert source.count("id(server_visual_received) = false;") >= 2
    assert "if (!id(server_visual_received))" in source
    for state in ("idle", "listening", "processing"):
        script = re.search(
            rf"  - id: set_visual_{state}\n(?P<body>.*?)(?=\n  - id: )",
            source,
            re.DOTALL,
        )
        assert script is not None
        assert "id(server_visual_received) = true;" in script.group("body")


@pytest.mark.parametrize("package_path", FIRMWARE_PACKAGES, ids=lambda path: path.stem)
def test_only_connection_and_server_visual_handlers_assign_main_visual_phase(
    package_path: Path,
) -> None:
    """MP-VISUAL-004/006: callbacks and local indicators cannot infer a main phase."""
    source = package_path.read_text(encoding="utf-8")
    assigned_phases = Counter(re.findall(r"id\(voice_assistant_phase\) = \$\{([^}]+)\};", source))

    if package_path.name.startswith("home-assistant"):
        assert assigned_phases == Counter(
            {
                "voice_assist_not_ready_phase_id": 2,
                "voice_assist_idle_phase_id": 1,
                "voice_assist_listening_for_command_phase_id": 1,
                "voice_assist_thinking_phase_id": 1,
            }
        )
    else:
        assert assigned_phases == Counter(
            {
                "voice_assist_not_ready_phase_id": 2,
                "voice_assist_idle_phase_id": 1,
                "voice_assist_listening_phase_id": 1,
                "voice_assist_thinking_phase_id": 1,
            }
        )
    assert "main visual remains server-controlled" in source


@pytest.mark.parametrize("package_path", FIRMWARE_PACKAGES, ids=lambda path: path.stem)
def test_explicit_stop_service_stops_device_pipeline(package_path: Path) -> None:
    """MP-STOP-001: the shared stop command reaches an explicit firmware stop."""
    source = package_path.read_text(encoding="utf-8")
    stop_script = re.search(
        r"  - id: stop_listening\n(?P<body>.*?)(?=\n  - id: )",
        source,
        re.DOTALL,
    )

    assert stop_script is not None
    assert "voice_assistant.stop:" in stop_script.group("body")


@pytest.mark.parametrize("package_path", FIRMWARE_PACKAGES, ids=lambda path: path.stem)
def test_continuous_mode_has_no_on_device_wake_word(package_path: Path) -> None:
    source = package_path.read_text(encoding="utf-8")
    assert "micro_wake_word" not in source
    assert "/home/maciek/piotr/wakeword" not in source
    assert "start_follow_up_listening" not in source
    start = re.search(
        r"  - id: start_open_mic_listening\n(?P<body>.*?)(?=\n  - id: )",
        source,
        re.DOTALL,
    )
    assert start is not None
    assert "voice_assistant.start_continuous:" in start.group("body")
    if package_path.name.startswith("home-assistant"):
        assert "\ni2s_audio:\n" in source


def test_shared_api_exposes_required_actions_and_cue_responses() -> None:
    source = API_SERVICES.read_text(encoding="utf-8")
    assert "start_follow_up_listening" not in source
    actions = re.findall(r"    - action: (\w+)", source)
    assert tuple(actions) == REQUIRED_ACTIONS

    visual = re.findall(r"    - action: set_visual_(\w+)", source)
    assert visual == ["idle", "listening", "processing"]

    for name in CUE_ACTIONS:
        block = re.search(
            rf"    - action: {name}\n(?P<body>.*?)(?=\n    - action: |\Z)",
            source,
            re.DOTALL,
        )
        assert block is not None, name
        body = block.group("body")
        assert "supports_response: status" in body, name
        assert "api.respond:" in body, name
        assert "success: true" in body, name
        assert f"script.execute: {name}" in body, name
        assert f"script.wait: {name}" in body, name


@pytest.mark.parametrize("package_path", FIRMWARE_PACKAGES, ids=lambda path: path.stem)
def test_packages_define_the_follow_up_ready_cue(package_path: Path) -> None:
    source = package_path.read_text(encoding="utf-8")
    assert re.search(r"  - id: play_follow_up_ready_cue\n", source)


def test_device_yaml_uses_secrets_and_gitignore_excludes_them() -> None:
    gitignore = (FIRMWARE_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/secrets.yaml" in gitignore
    assert not (FIRMWARE_ROOT / "secrets.yaml").exists()
    example = (FIRMWARE_ROOT / "secrets.example.yaml").read_text(encoding="utf-8")
    assert "YOUR_2_4_GHZ_WIFI_NAME" in example
    for path in DEVICE_YAML:
        source = path.read_text(encoding="utf-8")
        assert "ssid: !secret wifi_ssid" in source
        assert "password: !secret wifi_password" in source
        assert "key: !secret " in source
        assert "password: !secret " in source
        # No literal secret values in the repo files.
        assert re.search(r"key:\s+[\"'](?!\$\{)[^!]", source) is None
