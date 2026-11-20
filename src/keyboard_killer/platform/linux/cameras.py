"""Linux camera discovery (V4L2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from keyboard_killer.platform.base import CameraInfo

BY_ID_DIR = Path("/dev/v4l/by-id")


def discover_v4l_by_id(root: Path = BY_ID_DIR) -> list[str]:
    """Return sorted `/dev/v4l/by-id/*-video-index0` symlinks for capture devices."""
    if not root.exists():
        return []
    return sorted(str(p) for p in root.iterdir() if p.name.endswith("-video-index0") and p.is_symlink())


def list_cameras(root: Path = BY_ID_DIR) -> list[CameraInfo]:
    """Stable camera identities from udev by-id links (D5)."""
    cameras: list[CameraInfo] = []
    for path in discover_v4l_by_id(root):
        link = Path(path)
        cameras.append(
            CameraInfo(
                id=link.name,
                name=link.name,
                path=path,
                capabilities={"backend": "v4l2", "by_id": True},
            )
        )
    return cameras


def resolve_camera_config(
    requested_mode: str,
    cam1_by_id: str,
    cam2_by_id: str,
    *,
    discovered: list[str] | None = None,
    exists: Callable[[str], bool] | None = None,
) -> tuple[str, str, str]:
    """Resolve auto/mono/stereo into concrete device paths for this host.

    Returns ``(mode, cam1, cam2)``. In mono mode ``cam2`` is empty; in stereo
    mode both paths are populated. A missing configured camera falls back to a
    discovered stable device when one exists.
    """
    found = discover_v4l_by_id() if discovered is None else list(discovered)
    is_file = exists or (lambda path: bool(path) and Path(path).exists())
    cam1 = cam1_by_id if is_file(cam1_by_id) else (found[0] if found else cam1_by_id)
    mode = (requested_mode or "auto").lower()
    if mode not in {"auto", "mono", "stereo"}:
        mode = "auto"
    if mode == "mono":
        return "mono", cam1, ""
    if mode == "stereo":
        cam2 = cam2_by_id
        if not is_file(cam2) or cam2 == cam1:
            cam2 = next((path for path in found if path != cam1), "")
        return ("stereo", cam1, cam2) if cam2 else ("mono", cam1, "")
    # auto
    if cam2_by_id and cam2_by_id != cam1 and is_file(cam2_by_id):
        return "stereo", cam1, cam2_by_id
    other = next((path for path in found if path != cam1), "")
    if other:
        return "stereo", cam1, other
    return ("mono" if cam1 else "stereo"), cam1, ""
