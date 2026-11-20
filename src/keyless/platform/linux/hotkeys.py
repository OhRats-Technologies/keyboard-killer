"""Linux evdev hotkeys, physical-click harvesting, and cursor resolution."""

from __future__ import annotations

import glob
import math
import os
import selectors
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import evdev
import numpy as np
from evdev import InputDevice, ecodes

if TYPE_CHECKING:
    from keyless.vision.online_learner import OnlineGazeLearner

try:
    import gi  # type: ignore[import-not-found]

    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib  # type: ignore[import-not-found]

    _HAS_GIO = True
except Exception:
    _HAS_GIO = False


def resolve_hotkey_code(key_name: str) -> int:
    """Resolve an evdev key name (e.g. `KEY_RIGHTCTRL`, `BTN_SIDE`) to its numeric code."""
    normalized = key_name.strip().upper()
    if not normalized.startswith(("KEY_", "BTN_")):
        normalized = f"KEY_{normalized}"
    code = getattr(ecodes, normalized, None)
    if code is None:
        raise ValueError(f"Unknown evdev hotkey name: {key_name!r}")
    return int(code)


class EvdevHotkeyMonitor:
    """Monitors physical `/dev/input/event*` devices for a Wayland-agnostic PTT key."""

    def __init__(
        self,
        hotkey_code: int,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self.hotkey_code = int(hotkey_code)
        self._on_press = on_press
        self._on_release = on_release
        self._devices: list[InputDevice] = []
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="evdev-ptt-monitor", daemon=True)
        self._pressed = False

    def start(self) -> int:
        """Open matching readable input devices and start listening; returns device count."""
        for path in sorted(glob.glob("/dev/input/event*")):
            if not os.access(path, os.R_OK):
                continue
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if dev.name.startswith("keyless"):
                dev.close()
                continue
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            if self.hotkey_code in keys:
                self._devices.append(dev)
            else:
                dev.close()

        if self._devices:
            self._thread.start()
        return len(self._devices)

    def _run(self) -> None:
        sel = selectors.DefaultSelector()
        for dev in self._devices:
            sel.register(dev, selectors.EVENT_READ)
        try:
            while not self._stop_event.is_set():
                events = sel.select(timeout=0.1)
                for key, _ in events:
                    dev: InputDevice = key.fileobj  # type: ignore[assignment]
                    try:
                        for ev in dev.read():
                            if ev.type == ecodes.EV_KEY and ev.code == self.hotkey_code:
                                if ev.value == 1 and not self._pressed:
                                    self._pressed = True
                                    self._on_press()
                                elif ev.value == 0 and self._pressed:
                                    self._pressed = False
                                    self._on_release()
                    except OSError:
                        continue
        finally:
            sel.close()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        for dev in self._devices:
            try:
                dev.close()
            except OSError:
                pass
        self._devices.clear()


@dataclass
class _PendingClick:
    """Stores pre-click timestamp and physical mouse motion state while button is pressed."""

    down_ts: float
    pre_click_speed: float
    drag_distance_counts: float = 0.0


class WaylandCursorPositionResolver:
    """Resolves exact global Wayland cursor `(x, y)` via Mutter cursor-sprite diff in /dev/shm."""

    def __init__(self, allow_screenshot_diff: bool = False) -> None:
        self._shm_no_cur = Path("/dev/shm/kk_cur_0.bmp")
        self._shm_with_cur = Path("/dev/shm/kk_cur_1.bmp")
        self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None) if _HAS_GIO else None
        self._lock = threading.Lock()
        self.allow_screenshot_diff = bool(allow_screenshot_diff)

    def _dbus_full_screenshot(self, include_cursor: bool, out_path: Path) -> np.ndarray | None:
        if self._bus is not None:
            try:
                self._bus.call_sync(
                    "org.gnome.Shell.Screenshot",
                    "/org/gnome/Shell/Screenshot",
                    "org.gnome.Shell.Screenshot",
                    "Screenshot",
                    GLib.Variant("(bbs)", (bool(include_cursor), False, str(out_path))),
                    GLib.VariantType("(bs)"),
                    Gio.DBusCallFlags.NONE,
                    350,
                    None,
                )
                return cv2.imread(str(out_path), cv2.IMREAD_COLOR)
            except Exception:
                pass

        subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Shell.Screenshot",
                "--object-path",
                "/org/gnome/Shell/Screenshot",
                "--method",
                "org.gnome.Shell.Screenshot.Screenshot",
                "true" if include_cursor else "false",
                "false",
                str(out_path),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return cv2.imread(str(out_path), cv2.IMREAD_COLOR)

    def resolve_cursor_xy(
        self,
        hint_gaze_x: float,
        hint_gaze_y: float,
    ) -> tuple[int, int] | None:
        """Locate the hardware cursor hotspot in global desktop coordinates.

        Diffing two consecutive compositor frames (`include_cursor=False` vs `True`) isolates
        the 16x16..48x48 hardware cursor glyph on ANY native Wayland or OpenGL window.
        """
        with self._lock:
            if self.allow_screenshot_diff:
                img0 = self._dbus_full_screenshot(include_cursor=False, out_path=self._shm_no_cur)
                img1 = self._dbus_full_screenshot(include_cursor=True, out_path=self._shm_with_cur)
            else:
                img0 = None
                img1 = None

            if img0 is not None and img1 is not None and img0.shape == img1.shape:
                diff = np.max(cv2.absdiff(img1, img0), axis=2)
                mask = (diff > 18).astype(np.uint8)

                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
                closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

                num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed)
                best_hotspot: tuple[int, int] | None = None
                best_dist = float("inf")

                for i in range(1, num_labels):
                    x = int(stats[i, cv2.CC_STAT_LEFT])
                    y = int(stats[i, cv2.CC_STAT_TOP])
                    w = int(stats[i, cv2.CC_STAT_WIDTH])
                    h = int(stats[i, cv2.CC_STAT_HEIGHT])
                    area = int(stats[i, cv2.CC_STAT_AREA])

                    if 5 <= w <= 68 and 5 <= h <= 68 and 12 <= area <= 2200:
                        hx = x + min(5, w // 3)
                        hy = y + min(5, h // 3)
                        dist = math.hypot(hx - hint_gaze_x, hy - hint_gaze_y)
                        if dist < best_dist:
                            best_dist = dist
                            best_hotspot = (hx, hy)

                if best_hotspot is not None:
                    return best_hotspot

            try:
                out = subprocess.check_output(
                    ["xdotool", "getmouselocation", "--shell"],
                    stderr=subprocess.DEVNULL,
                    timeout=0.04,
                    text=True,
                )
                vals = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
                return int(vals["X"]), int(vals["Y"])
            except Exception:
                return None


class EvdevMouseClickHarvester:
    """Listens to physical mice/trackpads via evdev and feeds verified clicks to OnlineGazeLearner."""

    def __init__(
        self,
        online_learner: "OnlineGazeLearner",
        get_current_gaze_xy: Callable[[], tuple[float, float]],
        min_click_interval_sec: float = 0.45,
        max_hold_duration_sec: float = 0.35,
        max_drag_counts: float = 18.0,
        max_pre_click_speed_counts_s: float = 1400.0,
    ) -> None:
        self.online_learner = online_learner
        self.get_current_gaze_xy = get_current_gaze_xy
        self.min_click_interval_sec = min_click_interval_sec
        self.max_hold_duration_sec = max_hold_duration_sec
        self.max_drag_counts = max_drag_counts
        self.max_pre_click_speed_counts_s = max_pre_click_speed_counts_s

        self.resolver = WaylandCursorPositionResolver()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_harvest_ts = 0.0
        self._last_firefox_click_ts = 0.0

    def notify_firefox_click_received(self) -> None:
        """Call when Firefox extension already reported a passive click to avoid duplicate work."""
        self._last_firefox_click_ts = time.monotonic()

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _discover_physical_mice() -> list[evdev.InputDevice]:
        """Find all physical mice/trackpads with BTN_LEFT, excluding our virtual uinput devices."""
        devices: list[evdev.InputDevice] = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                name_low = (dev.name or "").lower()
                if "keyless" in name_low or "py-evdev-uinput" in name_low:
                    dev.close()
                    continue
                caps = dev.capabilities()
                keys = caps.get(ecodes.EV_KEY, [])
                rels = caps.get(ecodes.EV_REL, [])
                abss = caps.get(ecodes.EV_ABS, [])
                has_left_btn = ecodes.BTN_LEFT in keys
                has_xy_motion = (ecodes.REL_X in rels and ecodes.REL_Y in rels) or (
                    ecodes.ABS_X in abss and ecodes.ABS_Y in abss
                )
                if has_left_btn and has_xy_motion:
                    devices.append(dev)
                else:
                    dev.close()
            except Exception:
                continue
        return devices

    def _listen_loop(self) -> None:
        sel = selectors.DefaultSelector()
        active_devs: dict[int, evdev.InputDevice] = {}
        last_scan_ts = 0.0

        rel_history: deque[tuple[float, float, float]] = deque(maxlen=64)
        pending_click: _PendingClick | None = None

        def _refresh_devices() -> None:
            for fd, d in list(active_devs.items()):
                try:
                    sel.unregister(fd)
                    d.close()
                except Exception:
                    pass
            active_devs.clear()
            for d in self._discover_physical_mice():
                active_devs[d.fd] = d
                sel.register(d.fd, selectors.EVENT_READ, d)
            if active_devs:
                names = ", ".join(d.name for d in active_devs.values())
                print(f"[EvdevMouse] Monitoring physical pointing devices: {names}")

        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                if not active_devs and (now - last_scan_ts) >= 5.0:
                    last_scan_ts = now
                    _refresh_devices()
                    if not active_devs:
                        time.sleep(1.0)
                        continue

                events = sel.select(timeout=0.2)
                for key, _mask in events:
                    dev: evdev.InputDevice = key.data
                    try:
                        for ev in dev.read():
                            ev_ts = time.monotonic()

                            if ev.type == ecodes.EV_REL and ev.code in (ecodes.REL_X, ecodes.REL_Y):
                                dx = float(ev.value) if ev.code == ecodes.REL_X else 0.0
                                dy = float(ev.value) if ev.code == ecodes.REL_Y else 0.0
                                rel_history.append((ev_ts, dx, dy))
                                if pending_click is not None:
                                    pending_click.drag_distance_counts += abs(dx) + abs(dy)

                            elif ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_LEFT:
                                if ev.value == 1:
                                    recent_counts = sum(
                                        math.hypot(hx, hy)
                                        for hts, hx, hy in rel_history
                                        if (ev_ts - hts) <= 0.085
                                    )
                                    speed_counts_s = recent_counts / 0.085
                                    pending_click = _PendingClick(
                                        down_ts=ev_ts,
                                        pre_click_speed=speed_counts_s,
                                        drag_distance_counts=0.0,
                                    )

                                elif ev.value == 0 and pending_click is not None:
                                    click = pending_click
                                    pending_click = None
                                    hold_dur = ev_ts - click.down_ts

                                    if (
                                        hold_dur <= self.max_hold_duration_sec
                                        and click.drag_distance_counts <= self.max_drag_counts
                                        and click.pre_click_speed <= self.max_pre_click_speed_counts_s
                                        and (click.down_ts - self._last_harvest_ts)
                                        >= self.min_click_interval_sec
                                    ):
                                        self._last_harvest_ts = click.down_ts
                                        threading.Thread(
                                            target=self._resolve_and_feed_click,
                                            args=(click.down_ts,),
                                            daemon=True,
                                        ).start()
                    except OSError:
                        _refresh_devices()
                        break
        finally:
            for fd, d in list(active_devs.items()):
                try:
                    sel.unregister(fd)
                    d.close()
                except Exception:
                    pass
            sel.close()

    def _resolve_and_feed_click(self, click_down_ts: float) -> None:
        """Resolve global cursor `(x, y)` and record the pre-click fixation in OnlineGazeLearner."""
        time.sleep(0.025)
        if abs(self._last_firefox_click_ts - click_down_ts) < 0.090:
            return

        gx, gy = self.get_current_gaze_xy()
        cursor_xy = self.resolver.resolve_cursor_xy(hint_gaze_x=gx, hint_gaze_y=gy)
        if cursor_xy is None:
            return

        cx, cy = cursor_xy
        self.online_learner.record_verified_interaction(
            true_x=float(cx),
            true_y=float(cy),
            anchor_ts=click_down_ts,
            source="mouse",
            confidence_weight=1.8,
        )
