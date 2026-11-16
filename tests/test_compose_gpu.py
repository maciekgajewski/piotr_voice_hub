"""Compose GPU reservations use CDI, not the NVIDIA runtime hook (T-024)."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

CDI_GPU: dict[str, Any] = {
    "driver": "cdi",
    "device_ids": ["nvidia.com/gpu=all"],
    "capabilities": ["gpu"],
}

HOOK_ENV = frozenset({"NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"})


def compose_paths() -> list[Path]:
    found = sorted(ROOT.glob("docker-compose*.yml"))
    assert [path.name for path in found] == [
        "docker-compose.stt-oneshot.yml",
        "docker-compose.yml",
    ]
    return found


def load(path: Path) -> dict[str, Any]:
    loaded: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def services_of(doc: dict[str, Any]) -> dict[str, Any]:
    services = doc.get("services", {})
    assert isinstance(services, dict)
    return services


def reservations(service: dict[str, Any]) -> list[Any]:
    deploy = service.get("deploy")
    if deploy is None:
        return []
    assert isinstance(deploy, dict)
    resources = deploy.get("resources", {})
    assert isinstance(resources, dict)
    reserved = resources.get("reservations", {})
    assert isinstance(reserved, dict)
    devices = reserved.get("devices", [])
    assert isinstance(devices, list)
    return devices


def environment_names(service: dict[str, Any]) -> set[str]:
    env = service.get("environment")
    if env is None:
        return set()
    if isinstance(env, dict):
        return {str(key) for key in env}
    assert isinstance(env, list)
    names: set[str] = set()
    for item in env:
        assert isinstance(item, str)
        names.add(item.split("=", 1)[0])
    return names


def test_hub_and_piper_reserve_the_cdi_gpu() -> None:
    services = services_of(load(ROOT / "docker-compose.yml"))
    assert set(services) == {"hub", "piper", "speaker-recognition"}
    for name in ("hub", "piper"):
        service = services[name]
        assert isinstance(service, dict)
        assert reservations(service) == [CDI_GPU]
        assert "gpus" not in service
        assert "runtime" not in service
        assert not environment_names(service) & HOOK_ENV
    speaker = services["speaker-recognition"]
    assert isinstance(speaker, dict)
    assert reservations(speaker) == []
    assert "gpus" not in speaker
    assert "runtime" not in speaker


def test_no_compose_service_uses_the_nvidia_runtime_hook() -> None:
    gpu_services: set[str] = set()
    for path in compose_paths():
        for name, service in services_of(load(path)).items():
            assert isinstance(service, dict), f"{path.name} {name}"
            assert service.get("runtime") != "nvidia", f"{path.name} {name}"
            assert not environment_names(service) & HOOK_ENV, f"{path.name} {name}"
            devices = reservations(service)
            for device in devices:
                assert isinstance(device, dict)
                assert device.get("driver") != "nvidia", f"{path.name} {name}"
                assert "count" not in device
            if "gpus" in service or devices:
                assert devices == [CDI_GPU], f"{path.name} {name}"
                gpu_services.add(str(name))
    assert gpu_services == {"hub", "piper"}


def test_stt_override_does_not_touch_the_gpu_reservation() -> None:
    services = services_of(load(ROOT / "docker-compose.stt-oneshot.yml"))
    assert set(services) == {"hub"}
    hub = services["hub"]
    assert isinstance(hub, dict)
    assert "deploy" not in hub
    assert "gpus" not in hub
    assert "runtime" not in hub
    assert "environment" not in hub
