"""GNOME Mutter logical monitor layout (Linux)."""

from __future__ import annotations

import re
import subprocess

from keyboard_killer.platform.base import DisplayLayout, MonitorInfo


def parse_mutter_display_state(gvariant_text: str) -> DisplayLayout:
    """Parse `org.gnome.Mutter.DisplayConfig.GetCurrentState` gdbus output."""
    # Extract current physical resolution per connector by scanning monitor blocks.
    phys_modes: dict[str, tuple[int, int]] = {}
    conn_pattern = re.compile(r"\(\('([^']+)',\s*'[^']*',\s*'[^']*',\s*'[^']*'\),\s*\[")
    current_mode_pattern = re.compile(
        r"\('[^']+',\s*(\d+),\s*(\d+),\s*[\d.]+,\s*[\d.]+,\s*\[[^\]]*\],\s*\{[^}]*'is-current':\s*<true>"
    )

    conn_matches = list(conn_pattern.finditer(gvariant_text))
    for idx, m in enumerate(conn_matches):
        connector = m.group(1)
        start = m.end()
        end = conn_matches[idx + 1].start() if idx + 1 < len(conn_matches) else len(gvariant_text)
        block = gvariant_text[start:end]
        mode_m = current_mode_pattern.search(block)
        if mode_m:
            phys_modes[connector] = (int(mode_m.group(1)), int(mode_m.group(2)))

    layout_mode_m = re.search(r"'layout-mode':\s*<(?:uint32\s+)?(\d+)>", gvariant_text)
    layout_mode = int(layout_mode_m.group(1)) if layout_mode_m else 1

    logical_pattern = re.compile(
        r"\((-?\d+),\s*(-?\d+),\s*([\d.]+),\s*(?:uint32\s+)?\d+,\s*(true|false),\s*"
        r"\[\('([^']+)',\s*'[^']*',\s*'[^']*',\s*'[^']*'\)"
    )

    monitors: list[MonitorInfo] = []
    for m in logical_pattern.finditer(gvariant_text):
        x = int(m.group(1))
        y = int(m.group(2))
        scale = float(m.group(3))
        is_primary = m.group(4) == "true"
        connector = m.group(5)
        phys_w, phys_h = phys_modes.get(connector, (1920, 1080))
        if layout_mode == 1 and scale > 0:
            logical_w = int(round(phys_w / scale))
            logical_h = int(round(phys_h / scale))
        else:
            logical_w, logical_h = phys_w, phys_h
        monitors.append(
            MonitorInfo(
                connector=connector,
                x=x,
                y=y,
                width=logical_w,
                height=logical_h,
                physical_width=phys_w,
                physical_height=phys_h,
                scale=scale,
                is_primary=is_primary,
            )
        )

    if not monitors:
        fallback = MonitorInfo(
            connector="DEFAULT",
            x=0,
            y=0,
            width=1920,
            height=1080,
            physical_width=1920,
            physical_height=1080,
            scale=1.0,
            is_primary=True,
        )
        return DisplayLayout(monitors=(fallback,), total_width=1920, total_height=1080)

    total_w = max(mon.x + mon.width for mon in monitors)
    total_h = max(mon.y + mon.height for mon in monitors)
    return DisplayLayout(
        monitors=tuple(monitors),
        total_width=total_w,
        total_height=total_h,
        layout_mode=layout_mode,
    )


def get_display_layout() -> DisplayLayout:
    """Query active GNOME Mutter logical monitor layout, with safe fallback."""
    try:
        res = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Mutter.DisplayConfig",
                "--object-path",
                "/org/gnome/Mutter/DisplayConfig",
                "--method",
                "org.gnome.Mutter.DisplayConfig.GetCurrentState",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
        return parse_mutter_display_state(res.stdout)
    except (OSError, subprocess.SubprocessError):
        return parse_mutter_display_state("")
