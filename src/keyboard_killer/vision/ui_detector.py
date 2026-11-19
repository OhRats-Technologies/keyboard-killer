"""Gaze-foveated Wayland screenshot, OCR + OpenCV UI box detector, and Set-of-Marks renderer."""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from keyboard_killer.config import DisplayLayout, MonitorInfo
from keyboard_killer.config.paths import current_paths

logger = logging.getLogger(__name__)

DEFAULT_ICON_CACHE_DIR = current_paths().icon_cache_dir

try:
    from rapidocr_onnxruntime import RapidOCR

    _HAS_RAPIDOCR = True
except ImportError:
    RapidOCR = None  # type: ignore[assignment,misc]
    _HAS_RAPIDOCR = False

try:
    import gi  # type: ignore[import-not-found]

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi  # type: ignore[import-not-found]

    _HAS_ATSPI = True
except Exception:
    _HAS_ATSPI = False

try:
    from jeepney import DBusAddress, HeaderFields, MatchRule, new_method_call
    from jeepney.io.blocking import DBusConnection, open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse

    _HAS_JEEPNEY = True
except ImportError:
    _HAS_JEEPNEY = False


@dataclass(frozen=True)
class ActiveWindowContext:
    """Focused desktop window identity and screen rectangle."""

    app_id: str
    title: str
    rect: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class UIBoundingBox:
    """Single clickable candidate region in global desktop coordinates."""

    mark_id: int
    global_x1: int
    global_y1: int
    global_x2: int
    global_y2: int
    label: str  # OCR text if any, or "" for unlabeled icon/button contour
    kind: str  # "text", "button", or "icon"
    gaze_dist_px: float  # Euclidean distance from box center to user's gaze (gx, gy)

    @property
    def center_xy(self) -> tuple[int, int]:
        return (self.global_x1 + self.global_x2) // 2, (self.global_y1 + self.global_y2) // 2

    @property
    def width(self) -> int:
        return max(1, self.global_x2 - self.global_x1)

    @property
    def height(self) -> int:
        return max(1, self.global_y2 - self.global_y1)


@dataclass
class GazeROISnapshot:
    """Pre-processed gaze patch ready for instant keyword matching or VLM Set-of-Marks."""

    crop_x: int
    crop_y: int
    crop_w: int
    crop_h: int
    gaze_x: float
    gaze_y: float
    stereo_feat: np.ndarray | None
    raw_bgr: np.ndarray
    som_png_bytes: bytes
    boxes: list[UIBoundingBox]
    all_text_boxes: list[UIBoundingBox] = field(default_factory=list)
    full_desktop_bgr: np.ndarray | None = None


class WaylandScreenCapturer:
    """Tear-free area/desktop capture on Ubuntu GNOME Wayland via D-Bus."""

    def __init__(self, shm_path: Path | None = None) -> None:
        self._shm_path = shm_path or Path("/dev/shm/kk_gaze_roi.bmp")
        self._lock = threading.Lock()
        self._conn: DBusConnection | None = None
        self._shell_area_denied = False
        self._portal_match_rules: set[str] = set()

    def _get_conn(self) -> DBusConnection | None:
        if not _HAS_JEEPNEY:
            return None
        if self._conn is None:
            self._conn = open_dbus_connection(bus="SESSION")
        return self._conn

    def _try_gnome_shell_area(self, x: int, y: int, w: int, h: int) -> np.ndarray | None:
        """Try `org.gnome.Shell.Screenshot.ScreenshotArea` (~12ms when permitted)."""
        if self._shell_area_denied:
            return None
        conn = self._get_conn()
        if conn is not None:
            try:
                msg = new_method_call(
                    DBusAddress(
                        "/org/gnome/Shell/Screenshot",
                        bus_name="org.gnome.Shell.Screenshot",
                        interface="org.gnome.Shell.Screenshot",
                    ),
                    "ScreenshotArea",
                    "iiiibs",
                    (int(x), int(y), int(w), int(h), False, str(self._shm_path)),
                )
                reply = conn.send_and_get_reply(msg, timeout=1.0)
                ok = bool(reply.body[0]) if reply.body else False
                if ok and self._shm_path.exists():
                    img = cv2.imread(str(self._shm_path), cv2.IMREAD_COLOR)
                    if img is not None and img.size > 0:
                        return img
                self._shell_area_denied = True
                return None
            except DBusErrorResponse as exc:
                logger.debug("GNOME Shell ScreenshotArea denied/failed (%s); using portal.", exc)
                self._shell_area_denied = True
                return None
            except Exception as exc:
                logger.debug("GNOME Shell ScreenshotArea error (%s); using portal.", exc)
                self._shell_area_denied = True
                return None

        # Fallback to gdbus CLI if jeepney is unavailable
        try:
            res = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.gnome.Shell.Screenshot",
                    "--object-path",
                    "/org/gnome/Shell/Screenshot",
                    "--method",
                    "org.gnome.Shell.Screenshot.ScreenshotArea",
                    str(int(x)),
                    str(int(y)),
                    str(int(w)),
                    str(int(h)),
                    "false",
                    str(self._shm_path),
                ],
                check=False,
                capture_output=True,
                timeout=1.5,
            )
            if res.returncode == 0 and self._shm_path.exists():
                img = cv2.imread(str(self._shm_path), cv2.IMREAD_COLOR)
                if img is not None and img.size > 0:
                    return img
        except Exception:
            pass
        self._shell_area_denied = True
        return None

    def capture_full_desktop_portal(self) -> np.ndarray | None:
        """Capture full desktop via sanctioned `org.freedesktop.portal.Screenshot`."""
        conn = self._get_conn()
        if conn is None:
            return None
        try:
            token = f"kk_{time.monotonic_ns()}"
            sender = conn.unique_name[1:].replace(".", "_")
            req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

            if sender not in self._portal_match_rules:
                rule = MatchRule(
                    type="signal",
                    interface="org.freedesktop.portal.Request",
                    member="Response",
                )
                conn.send_and_get_reply(
                    new_method_call(
                        DBusAddress(
                            "/org/freedesktop/DBus",
                            bus_name="org.freedesktop.DBus",
                            interface="org.freedesktop.DBus",
                        ),
                        "AddMatch",
                        "s",
                        (rule.serialise(),),
                    ),
                    timeout=1.0,
                )
                self._portal_match_rules.add(sender)

            call_msg = new_method_call(
                DBusAddress(
                    "/org/freedesktop/portal/desktop",
                    bus_name="org.freedesktop.portal.Desktop",
                    interface="org.freedesktop.portal.Screenshot",
                ),
                "Screenshot",
                "sa{sv}",
                ("", {"handle_token": ("s", token), "interactive": ("b", False)}),
            )
            reply = conn.send_and_get_reply(call_msg, timeout=1.5)
            if reply.body:
                req_path = str(reply.body[0])

            deadline = time.monotonic() + 3.0
            uri: str | None = None
            while time.monotonic() < deadline:
                remaining = max(0.05, deadline - time.monotonic())
                msg = conn.receive(timeout=remaining)
                if (
                    msg.header.fields.get(HeaderFields.path) == req_path
                    and msg.header.fields.get(HeaderFields.member) == "Response"
                ):
                    code, results = msg.body
                    if code == 0 and "uri" in results:
                        uri = str(results["uri"][1])
                    break

            if not uri:
                return None
            file_path = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
            try:
                img = cv2.imread(file_path, cv2.IMREAD_COLOR)
            finally:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            return img
        except Exception as exc:
            logger.debug("Portal Screenshot failed: %s", exc)
            # Reset connection on unexpected D-Bus transport error
            try:
                if self._conn is not None:
                    self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._portal_match_rules.clear()
            return None

    def capture_rect_with_full(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        desktop_w: int | None = None,
        desktop_h: int | None = None,
        cached_full_bgr: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray | None]:
        """Capture `(x, y, w, h)` in logical desktop coords, returning `(crop_bgr, full_bgr)`."""
        if cached_full_bgr is not None:
            crop = self._crop_from_full(
                cached_full_bgr, x, y, w, h, desktop_w=desktop_w, desktop_h=desktop_h
            )
            return crop, cached_full_bgr

        with self._lock:
            area_img = self._try_gnome_shell_area(x, y, w, h)
            if area_img is not None:
                return area_img, None

            full_img = self.capture_full_desktop_portal()
            if full_img is not None and full_img.size > 0:
                crop = self._crop_from_full(
                    full_img, x, y, w, h, desktop_w=desktop_w, desktop_h=desktop_h
                )
                return crop, full_img

        raise RuntimeError(
            "Wayland screenshot failed (both GNOME Shell ScreenshotArea and XDG Portal Screenshot failed)."
        )

    def get_active_window_rect(
        self,
        screen_w: int,
        screen_h: int,
    ) -> tuple[int, int, int, int] | None:
        """Attempt to resolve active window `(x, y, w, h)` via AT-SPI root frame or XWayland."""
        if _HAS_ATSPI:
            try:
                desktop = Atspi.get_desktop(0)
                for i in range(desktop.get_child_count()):
                    app = desktop.get_child_at_index(i)
                    if not app:
                        continue
                    for j in range(app.get_child_count()):
                        win = app.get_child_at_index(j)
                        if not win:
                            continue
                        st = win.get_state_set()
                        if st and st.contains(Atspi.StateType.ACTIVE):
                            comp = win.get_component_iface()
                            if comp:
                                ext = comp.get_extents(Atspi.CoordType.SCREEN)
                                if ext.width >= 120 and ext.height >= 80:
                                    x = max(0, min(screen_w - 1, int(ext.x)))
                                    y = max(0, min(screen_h - 1, int(ext.y)))
                                    w = min(screen_w - x, int(ext.width))
                                    h = min(screen_h - y, int(ext.height))
                                    return x, y, w, h
            except Exception:
                pass

        try:
            out = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                stderr=subprocess.DEVNULL,
                timeout=0.04,
                text=True,
            )
            vals = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            wx, wy = int(vals["X"]), int(vals["Y"])
            ww, wh = int(vals["WIDTH"]), int(vals["HEIGHT"])
            if ww >= 120 and wh >= 80:
                x = max(0, min(screen_w - 1, wx))
                y = max(0, min(screen_h - 1, wy))
                return x, y, min(screen_w - x, ww), min(screen_h - y, wh)
        except Exception:
            pass

        return None

    def get_active_window_context(
        self,
        screen_w: int,
        screen_h: int,
        desktop_atspi: object | None = None,
    ) -> ActiveWindowContext:
        """Return `(app_id, title, rect)` of the focused window via AT-SPI or XWayland."""
        app_id = ""
        title = ""
        if desktop_atspi is not None and getattr(desktop_atspi, "available", False):
            try:
                raw_app, raw_title = desktop_atspi.get_active_window_info()  # type: ignore[attr-defined]
                app_id = str(raw_app or "").strip().lower()
                title = str(raw_title or "").strip()
            except Exception:
                pass
        elif _HAS_ATSPI:
            try:
                desktop = Atspi.get_desktop(0)
                for i in range(desktop.get_child_count()):
                    app = desktop.get_child_at_index(i)
                    if not app:
                        continue
                    for j in range(app.get_child_count()):
                        win = app.get_child_at_index(j)
                        if not win:
                            continue
                        st = win.get_state_set()
                        if st and st.contains(Atspi.StateType.ACTIVE):
                            app_id = (app.get_name() or "").strip().lower()
                            title = (win.get_name() or "").strip()
                            break
                    if app_id:
                        break
            except Exception:
                pass

        rect = self.get_active_window_rect(screen_w, screen_h)
        return ActiveWindowContext(app_id=app_id, title=title, rect=rect)

    def capture_rect(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Capture desktop rectangle `(x, y, w, h)`."""
        crop, _ = self.capture_rect_with_full(x, y, w, h)
        return crop

    @staticmethod
    def _crop_from_full(
        full_bgr: np.ndarray,
        x: int,
        y: int,
        w: int,
        h: int,
        desktop_w: int | None = None,
        desktop_h: int | None = None,
    ) -> np.ndarray:
        fh, fw = full_bgr.shape[:2]
        sx = (fw / float(desktop_w)) if desktop_w and desktop_w > 0 else 1.0
        sy = (fh / float(desktop_h)) if desktop_h and desktop_h > 0 else 1.0

        px1 = max(0, min(fw - 1, int(round(x * sx))))
        py1 = max(0, min(fh - 1, int(round(y * sy))))
        px2 = max(px1 + 1, min(fw, int(round((x + w) * sx))))
        py2 = max(py1 + 1, min(fh, int(round((y + h) * sy))))
        crop = full_bgr[py1:py2, px1:px2]
        if abs(sx - 1.0) > 0.02 or abs(sy - 1.0) > 0.02:
            crop = cv2.resize(crop, (max(1, int(w)), max(1, int(h))), interpolation=cv2.INTER_AREA)
        return crop.copy()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            self._portal_match_rules.clear()


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter + 1e-5)


def monitor_at_point(layout: DisplayLayout, x: float, y: float) -> MonitorInfo:
    """Return the monitor containing `(x, y)`, falling back to the primary monitor."""
    for mon in layout.monitors:
        if mon.x <= x < (mon.x + mon.width) and mon.y <= y < (mon.y + mon.height):
            return mon
    return layout.primary


def _slice_line_into_word_boxes(
    gray_crop: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    full_text: str,
) -> list[tuple[int, int, int, int, str, str]]:
    """Slice a multi-word OCR line (e.g. 'File Edit View Terminal') into individual word boxes.

    Uses character-proportional anchors snapped to vertical projection whitespace valleys
    so clicking a single word inside a merged toolbar or terminal line hits the exact word.
    """
    words = [w for w in full_text.strip().split() if w]
    if len(words) <= 1:
        return [(x1, y1, x2, y2, full_text.strip(), "word")]

    out: list[tuple[int, int, int, int, str, str]] = []
    # Also keep the full phrase box so multi-word queries ("New Conversation", "Ask anything...") match cleanly
    out.append((x1, y1, x2, y2, full_text.strip(), "phrase"))

    total_w = max(1, x2 - x1)
    patch = gray_crop[y1:y2, x1:x2]
    if patch.size > 0 and patch.shape[1] >= len(words) * 4:
        col_grad = np.mean(np.abs(np.diff(patch.astype(np.float32), axis=0)), axis=0)
    else:
        col_grad = None

    total_chars = sum(len(w) for w in words) + (len(words) - 1)
    cursor_chars = 0

    for w_str in words:
        w_start_frac = cursor_chars / float(total_chars)
        w_end_frac = (cursor_chars + len(w_str)) / float(total_chars)
        cursor_chars += len(w_str) + 1

        wx1 = int(round(w_start_frac * total_w))
        wx2 = int(round(w_end_frac * total_w))

        if col_grad is not None:
            for boundary_px, is_start in ((wx1, True), (wx2, False)):
                lo = max(0, boundary_px - 8)
                hi = min(len(col_grad) - 1, boundary_px + 8)
                if hi > lo:
                    valley = lo + int(np.argmin(col_grad[lo : hi + 1]))
                    if is_start:
                        wx1 = valley
                    else:
                        wx2 = valley

        if wx2 - wx1 >= 8:
            out.append((x1 + wx1, y1, x1 + wx2, y2, w_str, "word"))

    return out


class VisualIconBank:
    """Persistent template bank that learns & matches custom app icons/images in <3ms."""

    def __init__(self, cache_dir: Path | None = DEFAULT_ICON_CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        # Keyed by (app_id, label) where app_id == "" means global template
        self._templates: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_all()

    @staticmethod
    def _normalize_key(name: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        return clean or "icon"

    def _load_all(self) -> None:
        if self.cache_dir is None:
            return
        for png_path in self.cache_dir.rglob("*.png"):
            bgr = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
            if bgr is not None and bgr.size > 0:
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 40, 120)
                rel_parent = png_path.parent.relative_to(self.cache_dir)
                app_id = "" if str(rel_parent) == "." else self._normalize_key(str(rel_parent))
                self._templates[(app_id, png_path.stem.replace("_", " "))] = (bgr, edges)

    def save_icon_patch(self, label: str, bgr_crop: np.ndarray, app_id: str = "") -> None:
        """Save an icon/image patch so future voice commands find it in <3ms without the VLM."""
        if bgr_crop.size == 0 or bgr_crop.shape[0] < 10 or bgr_crop.shape[1] < 10:
            return
        key = self._normalize_key(label)
        norm_app = self._normalize_key(app_id) if app_id else ""
        if self.cache_dir is not None:
            out_dir = (self.cache_dir / norm_app) if norm_app else self.cache_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"{key}.png"
            cv2.imwrite(str(path), bgr_crop)
        gray = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 120)
        self._templates[(norm_app, key.replace("_", " "))] = (bgr_crop.copy(), edges)

    def find_matching_icons(
        self,
        screen_bgr: np.ndarray,
        screen_edges: np.ndarray,
        threshold: float = 0.78,
        app_id: str = "",
    ) -> list[tuple[int, int, int, int, str]]:
        """Scan active window screenshot for any learned icons using color + edge correlation."""
        if not self._templates:
            return []

        norm_app = self._normalize_key(app_id) if app_id else ""
        sh, sw = screen_bgr.shape[:2]
        matches: list[tuple[int, int, int, int, str]] = []

        for (tpl_app, label), (tpl_bgr, tpl_edges) in self._templates.items():
            if tpl_app and norm_app and tpl_app != norm_app:
                continue
            th, tw = tpl_bgr.shape[:2]
            if th >= sh or tw >= sw:
                continue

            res_col = cv2.matchTemplate(screen_bgr, tpl_bgr, cv2.TM_CCOEFF_NORMED)
            _, max_col, _, loc_col = cv2.minMaxLoc(res_col)

            res_edg = cv2.matchTemplate(screen_edges, tpl_edges, cv2.TM_CCOEFF_NORMED)
            score_at_peak = 0.65 * float(max_col) + 0.35 * float(res_edg[loc_col[1], loc_col[0]])

            if score_at_peak >= threshold:
                x, y = int(loc_col[0]), int(loc_col[1])
                matches.append((x, y, x + tw, y + th, label))

        return matches


class GazeUIDetector:
    """Extracts word-sliced OCR boxes, learned icon templates, and OpenCV icon/button contours."""

    def __init__(
        self,
        capturer: WaylandScreenCapturer | None = None,
        ocr_engine: object | None = None,
        enable_ocr: bool = True,
        capture_fn: Callable[[int, int, int, int], np.ndarray] | None = None,
        icon_cache_dir: Path | None = None,
    ) -> None:
        self.capturer = capturer or WaylandScreenCapturer()
        self._capture_fn = capture_fn
        default_cache = None if (capture_fn is not None and icon_cache_dir is None) else (icon_cache_dir or DEFAULT_ICON_CACHE_DIR)
        self.icon_bank = VisualIconBank(default_cache)
        if ocr_engine is not None:
            self.ocr = ocr_engine
        elif enable_ocr and _HAS_RAPIDOCR:
            self.ocr = RapidOCR()
        else:
            self.ocr = None

    def _detect_visual_ui_contours(
        self,
        bgr: np.ndarray,
        existing_rects: list[tuple[int, int, int, int]],
        edges: np.ndarray | None = None,
    ) -> list[tuple[int, int, int, int, str]]:
        """Detect standalone toolbar icons, image thumbnails, buttons, and inputs."""
        if edges is None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 45, 135)
        h_img, w_img = bgr.shape[:2]
        candidates: list[tuple[int, int, int, int, str]] = []

        # Pass 1: Tight morphological kernel for standalone icons (14x14 .. 68x68)
        k_icon = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        closed_icon = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k_icon)
        cnts_icon, _ = cv2.findContours(closed_icon, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        rects_icon = sorted((cv2.boundingRect(c) for c in cnts_icon), key=lambda r: r[2] * r[3])

        for x, y, w, h in rects_icon:
            if 14 <= w <= 68 and 14 <= h <= 68:
                aspect = w / float(h)
                if 0.60 <= aspect <= 1.65:
                    pad = 3
                    rect = (
                        max(0, x - pad),
                        max(0, y - pad),
                        min(w_img - 1, x + w + pad),
                        min(h_img - 1, y + h + pad),
                    )
                    if not any(_iou(rect, ex) > 0.30 for ex in existing_rects):
                        existing_rects.append(rect)
                        candidates.append((*rect, "icon"))

        # Pass 2: Wider horizontal kernel for buttons, tabs, and input boxes
        k_btn = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3))
        closed_btn = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k_btn)
        cnts_btn, _ = cv2.findContours(closed_btn, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        rects_btn = sorted((cv2.boundingRect(c) for c in cnts_btn), key=lambda r: r[2] * r[3])

        for x, y, w, h in rects_btn:
            if w < 24 or h < 16 or w > int(w_img * 0.85) or h > 110:
                continue
            aspect = w / float(h)
            if aspect < 0.5 or aspect > 18.0:
                continue
            rect = (x, y, x + w, y + h)
            if any(_iou(rect, ex) > 0.35 for ex in existing_rects):
                continue
            existing_rects.append(rect)
            candidates.append((x, y, x + w, y + h, "button"))

        return candidates

    def capture_and_annotate(
        self,
        gaze_x: float,
        gaze_y: float,
        screen_w: int,
        screen_h: int,
        crop_w: int = 760,
        crop_h: int = 540,
        stereo_feat: np.ndarray | None = None,
        crop_origin: tuple[int, int] | None = None,
        cached_full_bgr: np.ndarray | None = None,
        max_som_boxes: int = 42,
        use_active_window: bool = False,
    ) -> GazeROISnapshot:
        """Capture either a Gaze-Foveated ROI or the full Active Window and annotate words & icons."""
        if use_active_window and crop_origin is None:
            win_rect = self.capturer.get_active_window_rect(screen_w, screen_h)
            if win_rect is not None:
                cx, cy, cw, ch = win_rect
            else:
                cx, cy, cw, ch = 0, 0, screen_w, screen_h
        else:
            cw = max(1, min(int(crop_w), int(screen_w)))
            ch = max(1, min(int(crop_h), int(screen_h)))
            if crop_origin is not None:
                cx = int(round(max(0, min(screen_w - cw, crop_origin[0]))))
                cy = int(round(max(0, min(screen_h - ch, crop_origin[1]))))
            else:
                cx = int(round(max(0, min(screen_w - cw, gaze_x - cw // 2))))
                cy = int(round(max(0, min(screen_h - ch, gaze_y - ch // 2))))

        full_bgr: np.ndarray | None = cached_full_bgr
        if self._capture_fn is not None:
            bgr = self._capture_fn(cx, cy, cw, ch)
        else:
            bgr, full_bgr = self.capturer.capture_rect_with_full(
                cx,
                cy,
                cw,
                ch,
                desktop_w=screen_w,
                desktop_h=screen_h,
                cached_full_bgr=cached_full_bgr,
            )

        actual_h, actual_w = bgr.shape[:2]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 45, 135)

        raw_boxes: list[tuple[int, int, int, int, str, str]] = []
        occupied_rects: list[tuple[int, int, int, int]] = []

        # 1. Fast Template Bank: Check for previously learned custom icons (<3ms)
        for x1, y1, x2, y2, icon_label in self.icon_bank.find_matching_icons(bgr, edges):
            raw_boxes.append((x1, y1, x2, y2, icon_label, "template_icon"))
            occupied_rects.append((x1, y1, x2, y2))

        # 2. RapidOCR + Word-Level Projection Slicing
        if self.ocr is not None:
            ocr_res, _ = self.ocr(bgr)  # type: ignore[misc]
            if ocr_res:
                for pts, text, conf in ocr_res:
                    if float(conf) < 0.42 or not str(text).strip():
                        continue
                    xs = [int(p[0]) for p in pts]
                    ys = [int(p[1]) for p in pts]
                    x1, y1 = max(0, min(xs) - 3), max(0, min(ys) - 2)
                    x2, y2 = min(actual_w - 1, max(xs) + 3), min(actual_h - 1, max(ys) + 2)
                    occupied_rects.append((x1, y1, x2, y2))

                    sliced = _slice_line_into_word_boxes(gray, x1, y1, x2, y2, str(text))
                    raw_boxes.extend(sliced)

        # 3. OpenCV Unlabeled Icon / Image / Button Detection
        for x1, y1, x2, y2, kind in self._detect_visual_ui_contours(bgr, occupied_rects, edges=edges):
            raw_boxes.append((x1, y1, x2, y2, "", kind))

        # Build complete labeled-box list (in global coords) for full-screen keyword/template matching
        all_text_boxes: list[UIBoundingBox] = []
        for x1, y1, x2, y2, label, kind in raw_boxes:
            if not label:
                continue
            gx1, gy1, gx2, gy2 = cx + x1, cy + y1, cx + x2, cy + y2
            dist = math.hypot(0.5 * (gx1 + gx2) - gaze_x, 0.5 * (gy1 + gy2) - gaze_y)
            all_text_boxes.append(
                UIBoundingBox(
                    mark_id=0,
                    global_x1=gx1,
                    global_y1=gy1,
                    global_x2=gx2,
                    global_y2=gy2,
                    label=label,
                    kind=kind,
                    gaze_dist_px=dist,
                )
            )

        # Sort candidates by proximity to gaze so mark [1], [2], [3] are right where user looks
        local_gx = gaze_x - cx
        local_gy = gaze_y - cy
        scored: list[tuple[float, tuple[int, int, int, int, str, str]]] = []
        for item in raw_boxes:
            bx = 0.5 * (item[0] + item[2])
            by = 0.5 * (item[1] + item[3])
            dist = math.hypot(bx - local_gx, by - local_gy)
            weight = 0.70 if item[5] == "template_icon" else (0.85 if item[4] else 1.10)
            scored.append((dist * weight, item))

        scored.sort(key=lambda t: t[0])
        top_items = [t[1] for t in scored[:max_som_boxes]]

        # Render Set-of-Marks (SoM) image for gemma4:e4b
        som_canvas = bgr.copy()
        reticle_x = max(0, min(actual_w - 1, int(round(local_gx))))
        reticle_y = max(0, min(actual_h - 1, int(round(local_gy))))
        cv2.circle(som_canvas, (reticle_x, reticle_y), 12, (255, 190, 0), 2, cv2.LINE_AA)

        ui_boxes: list[UIBoundingBox] = []
        for idx, (x1, y1, x2, y2, label, kind) in enumerate(top_items, start=1):
            gx1, gy1, gx2, gy2 = cx + x1, cy + y1, cx + x2, cy + y2
            dist = math.hypot(0.5 * (gx1 + gx2) - gaze_x, 0.5 * (gy1 + gy2) - gaze_y)
            ui_boxes.append(
                UIBoundingBox(
                    mark_id=idx,
                    global_x1=gx1,
                    global_y1=gy1,
                    global_x2=gx2,
                    global_y2=gy2,
                    label=label,
                    kind=kind,
                    gaze_dist_px=dist,
                )
            )

            box_col = (40, 240, 80) if label else (0, 200, 255)
            cv2.rectangle(som_canvas, (x1, y1), (x2, y2), box_col, 2)

            tag = str(idx)
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            tx1 = max(0, x1)
            ty1 = max(th + 4, y1)
            cv2.rectangle(som_canvas, (tx1, ty1 - th - 4), (tx1 + tw + 6, ty1 + 2), (12, 12, 12), -1)
            cv2.rectangle(som_canvas, (tx1, ty1 - th - 4), (tx1 + tw + 6, ty1 + 2), box_col, 1)
            cv2.putText(
                som_canvas,
                tag,
                (tx1 + 3, ty1 - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

        if actual_w > 1920:
            scale = 1920.0 / float(actual_w)
            som_canvas = cv2.resize(
                som_canvas,
                (1920, max(1, int(round(actual_h * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        ok, enc = cv2.imencode(".png", som_canvas)
        png_bytes = enc.tobytes() if ok else b""

        return GazeROISnapshot(
            crop_x=cx,
            crop_y=cy,
            crop_w=cw,
            crop_h=ch,
            gaze_x=gaze_x,
            gaze_y=gaze_y,
            stereo_feat=stereo_feat,
            raw_bgr=bgr,
            som_png_bytes=png_bytes,
            boxes=ui_boxes,
            all_text_boxes=all_text_boxes,
            full_desktop_bgr=full_bgr,
        )

    def close(self) -> None:
        self.capturer.close()
