"""Resolve the calibrated gaze area inside the virtual desktop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from keyboard_killer.platform.base import DisplayLayout


@dataclass(frozen=True)
class GazeRegion:
    """Rectangle in virtual-screen pixels that the current calibration covers."""

    x: int
    y: int
    width: int
    height: int
    connector: str = ""
    source_x: float = 0.0
    source_y: float = 0.0
    source_width: float = 0.0
    source_height: float = 0.0

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def full_region(layout: DisplayLayout) -> GazeRegion:
    return GazeRegion(
        0,
        0,
        max(1, layout.total_width),
        max(1, layout.total_height),
        source_width=float(max(1, layout.total_width)),
        source_height=float(max(1, layout.total_height)),
    )


def calibration_region(path: Path, layout: DisplayLayout) -> GazeRegion:
    """Read the calibration bounds and place them on their calibrated monitor.

    Calibration targets are stored in monitor-local coordinates.  A future
    whole-desktop calibration stores a region wider than one monitor; that is
    treated as virtual-desktop coordinates so it is not offset twice.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return full_region(layout)
    if str(data.get("version") or "").startswith("geo-"):
        return full_region(layout)
    bounds = data.get("bounds") or {}
    connector = str(data.get("monitor_connector") or "")
    monitor = next((item for item in layout.monitors if item.connector == connector), None)
    if monitor is None:
        monitor = layout.primary
    try:
        min_x = float(bounds.get("min_x", 0.0))
        min_y = float(bounds.get("min_y", 0.0))
        max_x = float(bounds.get("max_x", monitor.width - 1))
        max_y = float(bounds.get("max_y", monitor.height - 1))
    except (TypeError, ValueError):
        return full_region(layout)
    width = max(1, int(round(max_x - min_x + 1)))
    height = max(1, int(round(max_y - min_y + 1)))
    source = {
        "source_x": min_x,
        "source_y": min_y,
        "source_width": float(width),
        "source_height": float(height),
    }
    if width > monitor.width * 1.25:
        # The stored bounds already span more than one monitor.
        return GazeRegion(
            int(round(min_x)),
            int(round(min_y)),
            width,
            height,
            connector,
            **source,
        )
    return GazeRegion(
        monitor.x + int(round(min_x)),
        monitor.y + int(round(min_y)),
        width,
        height,
        connector,
        **source,
    )


def region_args(region: GazeRegion) -> list[str]:
    return [
        "--live-x",
        str(region.x),
        "--live-y",
        str(region.y),
        "--live-width",
        str(region.width),
        "--live-height",
        str(region.height),
        "--live-source-x",
        str(region.source_x),
        "--live-source-y",
        str(region.source_y),
        "--live-source-width",
        str(region.source_width or region.width),
        "--live-source-height",
        str(region.source_height or region.height),
    ]


__all__ = ["GazeRegion", "calibration_region", "full_region", "region_args"]
