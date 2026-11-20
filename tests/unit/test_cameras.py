"""One/two camera resolution contract."""

from __future__ import annotations

from keyless.platform.linux.cameras import resolve_camera_config


def _exists(path: str) -> bool:
    return path in {"cam-a", "cam-b"}


def test_auto_uses_two_when_available() -> None:
    assert resolve_camera_config(
        "auto", "cam-a", "cam-b", discovered=["cam-a", "cam-b"], exists=_exists
    ) == ("stereo", "cam-a", "cam-b")


def test_auto_falls_back_to_one_camera() -> None:
    mode, cam1, cam2 = resolve_camera_config(
        "auto", "missing", "", discovered=["cam-a"], exists=_exists
    )
    assert mode == "mono"
    assert cam1 == "cam-a"
    assert cam2 == ""


def test_mono_never_requires_a_second_camera() -> None:
    assert resolve_camera_config(
        "mono", "cam-a", "cam-b", discovered=["cam-a", "cam-b"], exists=_exists
    ) == ("mono", "cam-a", "")


def test_explicit_stereo_degrades_to_mono_when_only_one_exists() -> None:
    assert resolve_camera_config(
        "stereo", "cam-a", "missing", discovered=["cam-a"], exists=_exists
    ) == ("mono", "cam-a", "")
