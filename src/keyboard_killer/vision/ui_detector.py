"""Gaze-foveated Wayland screenshot, OCR + OpenCV UI box detector, and Set-of-Marks renderer."""

from __future__ import annotations

import logging
import math
import os
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

logger = logging.getLogger(__name__)

try:
    from rapidocr_onnxruntime import RapidOCR

    _HAS_RAPIDOCR = True
except ImportError:
    RapidOCR = None  # type: ignore[assignment,misc]
    _HAS_RAPIDOCR = False

try:
    from jeepney import DBusAddress, HeaderFields, MatchRule, new_method_call
    from jeepney.io.blocking import DBusConnection, open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse

    _HAS_JEEPNEY = True
except ImportError:
    _HAS_JEEPNEY = False


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


class GazeUIDetector:
    """Extracts OCR text boxes + OpenCV button/icon contours and renders Set-of-Marks overlays."""

    def __init__(
        self,
        capturer: WaylandScreenCapturer | None = None,
        ocr_engine: object | None = None,
        enable_ocr: bool = True,
        capture_fn: Callable[[int, int, int, int], np.ndarray] | None = None,
    ) -> None:
        self.capturer = capturer or WaylandScreenCapturer()
        self._capture_fn = capture_fn
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
    ) -> list[tuple[int, int, int, int, str]]:
        """Find unlabeled icons, buttons, and input boxes via multi-scale morphological edges."""
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 45, 135)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[int, int, int, int, str]] = []
        h_img, w_img = bgr.shape[:2]

        # Sort smaller contours first so buttons/checkboxes inside popups aren't shadowed by frames
        bounded = [cv2.boundingRect(c) for c in contours]
        bounded.sort(key=lambda r: r[2] * r[3])

        for x, y, w, h in bounded:
            # Filter to plausible icon (14x14..52x52) and button/input (28x16..85% width x 110px) sizes
            if w < 14 or h < 14 or w > int(w_img * 0.85) or h > 110:
                continue
            aspect = w / float(h)
            if aspect < 0.45 or aspect > 16.0:
                continue
            rect = (x, y, x + w, y + h)
            if any(_iou(rect, ex) > 0.45 for ex in existing_rects):
                continue
            kind = "icon" if (w <= 52 and h <= 52 and 0.65 <= aspect <= 1.55) else "button"
            existing_rects.append(rect)
            candidates.append((x, y, x + w, y + h, kind))

        return candidates

    def capture_and_annotate(
        self,
        gaze_x: float,
        gaze_y: float,
        screen_w: int,
        screen_h: int,
        crop_w: int = 680,
        crop_h: int = 480,
        stereo_feat: np.ndarray | None = None,
        crop_origin: tuple[int, int] | None = None,
        cached_full_bgr: np.ndarray | None = None,
        max_som_boxes: int = 36,
    ) -> GazeROISnapshot:
        """Capture ROI centered on `(gaze_x, gaze_y)` (or at `crop_origin`) and compute SoM boxes."""
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
        raw_boxes: list[tuple[int, int, int, int, str, str]] = []
        occupied_rects: list[tuple[int, int, int, int]] = []

        # 1. Extract OCR text bounding boxes
        if self.ocr is not None:
            ocr_res, _ = self.ocr(bgr)  # type: ignore[misc]
            if ocr_res:
                for pts, text, conf in ocr_res:
                    if float(conf) < 0.45 or not str(text).strip():
                        continue
                    xs = [int(p[0]) for p in pts]
                    ys = [int(p[1]) for p in pts]
                    x1, y1 = max(0, min(xs) - 4), max(0, min(ys) - 3)
                    x2, y2 = min(actual_w - 1, max(xs) + 4), min(actual_h - 1, max(ys) + 3)
                    raw_boxes.append((x1, y1, x2, y2, str(text).strip(), "text"))
                    occupied_rects.append((x1, y1, x2, y2))

        # 2. Extract unlabeled buttons, inputs, and icons around gaze
        for x1, y1, x2, y2, kind in self._detect_visual_ui_contours(bgr, occupied_rects):
            raw_boxes.append((x1, y1, x2, y2, "", kind))

        # Build complete text-box list (in global coords) for Tier-3 full-screen keyword matching
        all_text_boxes: list[UIBoundingBox] = []
        for x1, y1, x2, y2, label, kind in raw_boxes:
            if kind != "text" or not label:
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

        # Sort boxes by proximity to user's gaze point so [1], [2], [3] are closest to fovea
        local_gx = gaze_x - cx
        local_gy = gaze_y - cy
        scored: list[tuple[float, tuple[int, int, int, int, str, str]]] = []
        for item in raw_boxes:
            bx = 0.5 * (item[0] + item[2])
            by = 0.5 * (item[1] + item[3])
            dist = math.hypot(bx - local_gx, by - local_gy)
            priority_dist = dist * (0.85 if item[5] == "text" else 1.15)
            scored.append((priority_dist, item))

        scored.sort(key=lambda t: t[0])
        top_items = [t[1] for t in scored[:max_som_boxes]]

        # Render Set-of-Marks (SoM) image with high-contrast numbered tags
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

            box_col = (40, 240, 80) if kind == "text" else (0, 200, 255)
            cv2.rectangle(som_canvas, (x1, y1), (x2, y2), box_col, 2)

            tag = str(idx)
            (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 2)
            tx1 = max(0, x1)
            ty1 = max(th + 4, y1)
            cv2.rectangle(som_canvas, (tx1, ty1 - th - 4), (tx1 + tw + 6, ty1 + 2), (15, 15, 15), -1)
            cv2.rectangle(som_canvas, (tx1, ty1 - th - 4), (tx1 + tw + 6, ty1 + 2), box_col, 1)
            cv2.putText(
                som_canvas,
                tag,
                (tx1 + 3, ty1 - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        # Cap huge Tier-3 canvases so VLM token count stays low while global coords stay exact
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
