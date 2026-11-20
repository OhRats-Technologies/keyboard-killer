"""Linux camera discovery (V4L2)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from keyboard_killer.platform.base import CameraInfo

BY_ID_DIR = Path("/dev/v4l/by-id")


def friendly_camera_name(path_or_id: str) -> str:
    """Turn a raw udev by-id camera string into a clean human-readable device name."""
    if not path_or_id:
        return "No camera"
    raw = Path(path_or_id).name
    raw = re.sub(r"^(?:usb|pci-[^-]+-usb-[^-]+)-", "", raw)
    raw = re.sub(r"-video-index\d+$", "", raw)
    # Remove noisy corporate suffixes and serial numbers
    raw = re.sub(r"_+Co\._*Ltd\.?_+", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"_[0-9A-Fa-f]{6,}$", "", raw)
    tokens = [t for t in re.split(r"[_\-\s]+", raw) if t]
    if not tokens:
        return Path(path_or_id).name
    # Collapse consecutive repeated phrases (e.g. GENERAL WEBCAM GENERAL WEBCAM)
    deduped: list[str] = []
    i = 0
    while i < len(tokens):
        matched = False
        for span in range(min(4, (len(tokens) - i) // 2), 0, -1):
            a = [t.lower() for t in tokens[i : i + span]]
            b = [t.lower() for t in tokens[i + span : i + 2 * span]]
            if a == b:
                deduped.extend(tokens[i : i + span])
                i += 2 * span
                matched = True
                break
        if not matched:
            if not deduped or deduped[-1].lower() != tokens[i].lower():
                deduped.append(tokens[i])
            i += 1
    # Drop pure hex vendor/product IDs like 046d 0825 when descriptive words remain
    non_hex = [t for t in deduped if not re.fullmatch(r"[0-9A-Fa-f]{4}", t)]
    words = non_hex if non_hex else deduped
    out = []
    for w in words:
        if w.isupper() and len(w) <= 3:
            out.append(w)
        elif w.isupper() or w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


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
                name=friendly_camera_name(link.name),
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
