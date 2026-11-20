"""Unit tests for the shared doctor report."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from keyboard_killer.config.paths import HOME_ENV, default_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.diagnostics.doctor import collect_doctor, format_text
from keyboard_killer.platform.base import (
    CapabilityInfo,
    CapabilityStatus,
    PermissionInfo,
    PermissionState,
)
from keyboard_killer.platform.capabilities import Capability, CapabilityReport


class _FakeSoundDevice:
    default = SimpleNamespace(device=(2, 5))

    @staticmethod
    def query_devices():
        return [
            {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 48000.0},
            {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0},
        ]


def _report() -> CapabilityReport:
    report = CapabilityReport(platform="linux")
    for capability in (Capability.POINTER, Capability.CAMERAS, Capability.AUDIO, Capability.STT):
        report.set(CapabilityInfo(capability.value, CapabilityStatus.AVAILABLE, "ok"))
    report.permissions = {
        "microphone": PermissionInfo("microphone", PermissionState.GRANTED, "ok"),
    }
    return report


def _paths(tmp_path: Path):
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    return paths


def test_doctor_report_serializes_and_matches_ui_fields(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    cam_a = tmp_path / "cam-a"
    cam_a.write_bytes(b"fake")
    cameras = [SimpleNamespace(id="cam-a", name="Cam A", path=str(cam_a), capabilities={})]
    report = collect_doctor(
        paths=paths,
        config=AppConfig(cam1_by_id=str(cam_a), cam2_by_id=str(tmp_path / "cam-missing")),
        capabilities=_report(),
        sounddevice_module=_FakeSoundDevice(),
        camera_lister=lambda: cameras,
        check_providers=False,
    )
    payload = json.loads(report.to_json())

    assert payload["paths"]["config"] == str(paths.config_dir)
    assert payload["path_checks"]["config"]["writable"] is True
    assert payload["capabilities"]["capabilities"]["pointer"]["status"] == "available"
    assert payload["permissions"]["microphone"]["state"] == "granted"
    assert payload["cameras"]["configured"]["cam1"]["readable"] is True
    assert payload["cameras"]["configured"]["cam2"]["exists"] is False
    assert payload["audio"]["inputs"][0]["name"] == "USB Mic"
    assert payload["audio"]["default"] == [2, 5]
    assert payload["stt"]["selected"] in {"parakeet", "missing", "unavailable"}
    assert payload["providers"]["text_llm"]["status"]["state"] == "not_checked"
    assert payload["keychain"]["service"] == "keyboard-killer"
    assert "Keyboard-Killer" in format_text(report)


def test_doctor_ok_requires_core_capabilities(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    report = collect_doctor(
        paths=paths,
        config=AppConfig(),
        capabilities=_report(),
        sounddevice_module=_FakeSoundDevice(),
        camera_lister=lambda: [],
        check_providers=False,
    )
    assert report.ok is True

    degraded = _report()
    degraded.set(CapabilityInfo(Capability.STT.value, CapabilityStatus.UNAVAILABLE, "missing"))
    broken = collect_doctor(
        paths=paths,
        config=AppConfig(),
        capabilities=degraded,
        sounddevice_module=_FakeSoundDevice(),
        camera_lister=lambda: [],
        check_providers=False,
    )
    assert broken.ok is False
