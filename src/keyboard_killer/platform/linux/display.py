"""Desktop logical monitor layout (Linux).

GNOME exposes the layout through Mutter's D-Bus API. KDE exposes the same
geometry through ``kscreen-doctor``; without that fallback a KDE session
silently reports a single 1920x1080 monitor and gaze coordinates land on the
wrong screen.
"""

from __future__ import annotations

import json
import os
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


def parse_kscreen_layout(text: str) -> DisplayLayout:
    """Parse the JSON printed by ``kscreen-doctor -j`` on Plasma."""
    data = json.loads(text)
    monitors: list[MonitorInfo] = []
    for output in data.get("outputs", []):
        if not output.get("enabled"):
            continue
        pos = output.get("pos") or {}
        size = output.get("size") or {}
        scale = float(output.get("scale") or 1.0)
        physical_w = int(size.get("width") or 0)
        physical_h = int(size.get("height") or 0)
        if physical_w <= 0 or physical_h <= 0:
            continue
        monitors.append(
            MonitorInfo(
                connector=str(output.get("name") or "DEFAULT"),
                x=int(pos.get("x") or 0),
                y=int(pos.get("y") or 0),
                width=int(round(physical_w / scale)),
                height=int(round(physical_h / scale)),
                physical_width=physical_w,
                physical_height=physical_h,
                scale=scale,
                is_primary=int(output.get("priority") or 1) == 1,
            )
        )
    if not monitors:
        raise ValueError("kscreen-doctor reported no enabled outputs")
    return DisplayLayout(
        monitors=tuple(monitors),
        total_width=max(mon.x + mon.width for mon in monitors),
        total_height=max(mon.y + mon.height for mon in monitors),
    )


def get_display_layout() -> DisplayLayout:
    """Query the active logical monitor layout, with a safe single-screen fallback."""
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
        layout = parse_mutter_display_state(res.stdout)
        if layout.monitors and layout.monitors[0].connector != "DEFAULT":
            return layout
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    try:
        # kscreen-doctor is itself a Qt app; a host that forced a platform for
        # its own window (the overlay's xcb child, headless tests) must not make
        # the helper report an empty screen list.
        env = {key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"}
        res = subprocess.run(
            ["kscreen-doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
            env=env,
        )
        return parse_kscreen_layout(res.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return parse_mutter_display_state("")
