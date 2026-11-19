"""Linux camera discovery (V4L2)."""

from __future__ import annotations

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
