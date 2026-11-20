"""Standalone click-through gaze cursor overlay for X11 and XWayland.

Wayland does not let a normal client place a top-level window at an arbitrary
screen position, so the main app launches this module with Qt's ``xcb`` plugin
whenever an X display is available.  One shared motion engine reads the
daemon's gaze snapshot, filters fixations/searching, and drives one overlay
window per monitor.  Without live data it plays a labeled demo inside the same
calibrated region, so the preview never claims more coverage than live gaze.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from keyless.platform.linux.display import get_display_layout
from keyless.ui.cursor_motion import GazeMotion, MotionSettings
from keyless.ui.gaze_art import cursor_bounds, demo_gaze, draw_gaze_cursor
from keyless.ui.gaze_region import GazeRegion, full_region
from keyless.ui.gaze_source import LiveGaze

TICK_MS = 16
DEMO_LABEL = "DEMO"
NO_FACE_LABEL = "NO FACE"
CURSOR_SCALE = 1.0


def _alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(frozen=True)
class CursorFrame:
    point: QPointF
    mode: str
    blinking: bool
    label: str = ""


class CursorEngine:
    """Shared gaze target, demo path, and fixation-aware spring state."""

    def __init__(
        self,
        *,
        live_region: GazeRegion,
        demo_only: bool = False,
        started: float | None = None,
    ) -> None:
        self.live_region = live_region
        self.demo_only = demo_only
        self._started = started if started is not None else time.monotonic()
        self._gaze = LiveGaze()
        self._motion = GazeMotion(
            MotionSettings(
                stiffness=190.0,
                damping_ratio=1.0,
                fixation_radius=30.0,
                fixation_break_radius=55.0,
                fixation_dwell_s=0.13,
                search_spread=110.0,
                search_stiffness_scale=0.46,
                search_damping_scale=1.6,
            )
        )
        self._last_tick = 0.0
        self._last_frame: CursorFrame | None = None
        self._mode = ""

    def _live_point(self, x: float, y: float) -> QPointF:
        region = self.live_region
        source_w = max(1.0, region.source_width or region.width)
        source_h = max(1.0, region.source_height or region.height)
        nx = min(1.0, max(0.0, (x - region.source_x) / source_w))
        ny = min(1.0, max(0.0, (y - region.source_y) / source_h))
        return QPointF(
            region.x + nx * region.width,
            region.y + ny * region.height,
        )

    def _demo_point(self, elapsed: float) -> QPointF:
        region = self.live_region
        nx, ny = demo_gaze(elapsed)
        return QPointF(
            region.x + nx * region.width,
            region.y + ny * region.height,
        )

    @staticmethod
    def label_for(mode: str) -> str:
        if mode == "demo":
            return DEMO_LABEL
        if mode == "no-face":
            return NO_FACE_LABEL
        return ""

    def frame(self, now: float | None = None) -> CursorFrame:
        """Return one shared frame; extra overlay timers reuse the same update."""
        stamp = time.monotonic() if now is None else now
        if self._last_frame is not None and stamp - self._last_tick < 0.008:
            return self._last_frame
        dt = stamp - self._last_tick if self._last_tick > 0.0 else 1.0 / 60.0
        self._last_tick = stamp

        sample = None
        if not self.demo_only:
            sample = self._gaze.read()
        if sample is not None and sample.live:
            target = self._live_point(sample.x, sample.y)
            mode = "live" if sample.face else "no-face"
            active = sample.face
            blinking = sample.blinking
        else:
            target = self._demo_point(stamp - self._started)
            mode = "demo"
            active = False
            blinking = False

        if mode != self._mode:
            self._motion.reset(target.x(), target.y(), stamp)
            self._mode = mode

        if mode == "demo":
            point = target
        else:
            mx, my, _ = self._motion.update(
                target.x(),
                target.y(),
                dt,
                active=active,
                blinking=blinking,
            )
            point = QPointF(mx, my)

        self._last_frame = CursorFrame(
            point=point,
            mode=mode,
            blinking=blinking,
            label=self.label_for(mode),
        )
        return self._last_frame

    def close(self) -> None:
        self._gaze.close()


class CursorOverlay(QWidget):
    """One transparent surface per monitor; paints only near the cursor."""

    def __init__(self, *, screen: QScreen, engine: CursorEngine) -> None:
        super().__init__()
        self.engine = engine
        self.window_geometry = screen.availableGeometry()
        self._last_point: QPointF | None = None
        self._label = ""

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
            # A native X11 session has no Wayland surfaces to stack above, so
            # bypass the window manager to avoid tiling and focus rules.
            flags |= Qt.WindowType.BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(self.window_geometry)
        self.setWindowTitle(f"Keyless Gaze Cursor ({screen.name()})")

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        frame = self.engine.frame()
        local = frame.point - QPointF(self.window_geometry.topLeft())
        inside = (
            0 <= local.x() <= self.width()
            and 0 <= local.y() <= self.height()
        )
        if inside:
            label = frame.label
            if self._last_point is None:
                self.update(self._cursor_rect(local, label))
            else:
                old = self._cursor_rect(self._last_point, self._label)
                new = self._cursor_rect(local, label)
                self.update(old.united(new))
            self._last_point = local
            self._label = label
        elif self._last_point is not None:
            self.update(self._cursor_rect(self._last_point, self._label))
            self._last_point = None
            self._label = ""

    def _cursor_rect(self, point: QPointF, label: str) -> QRect:
        bounds = cursor_bounds(CURSOR_SCALE)
        rect = QRect(
            int(point.x() + bounds.left()) - 8,
            int(point.y() + bounds.top()) - 8,
            int(bounds.width()) + 16,
            int(bounds.height()) + 16,
        )
        return rect

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if self._last_point is None:
            painter.end()
            return
        draw_gaze_cursor(
            painter,
            self._last_point,
            scale=CURSOR_SCALE,
            label=self._label,
        )
        painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keyless gaze cursor overlay")
    parser.add_argument("--demo", action="store_true", help="ignore live gaze and animate a demo")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits")
    parser.add_argument("--duration", type=float, default=None, help="exit after N seconds")
    parser.add_argument("--source-width", type=int, default=None)
    parser.add_argument("--source-height", type=int, default=None)
    parser.add_argument("--live-x", type=int, default=None)
    parser.add_argument("--live-y", type=int, default=None)
    parser.add_argument("--live-width", type=int, default=None)
    parser.add_argument("--live-height", type=int, default=None)
    parser.add_argument("--live-source-x", type=float, default=0.0)
    parser.add_argument("--live-source-y", type=float, default=0.0)
    parser.add_argument("--live-source-width", type=float, default=None)
    parser.add_argument("--live-source-height", type=float, default=None)
    return parser.parse_args(argv)


def _resolve_region(args: argparse.Namespace) -> tuple[GazeRegion, tuple[int, int]]:
    try:
        layout = get_display_layout()
    except Exception:
        layout = None
    if layout is None:
        region = GazeRegion(0, 0, 1920, 1080, source_width=1920, source_height=1080)
        source = (1920, 1080)
    else:
        region = full_region(layout)
        source = (layout.total_width, layout.total_height)
    if args.live_width and args.live_height:
        region = GazeRegion(
            x=args.live_x if args.live_x is not None else region.x,
            y=args.live_y if args.live_y is not None else region.y,
            width=args.live_width,
            height=args.live_height,
            source_x=args.live_source_x,
            source_y=args.live_source_y,
            source_width=args.live_source_width or args.live_width,
            source_height=args.live_source_height or args.live_height,
        )
    if args.source_width and args.source_height:
        source = (args.source_width, args.source_height)
    return region, source


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    region, _source = _resolve_region(args)

    QApplication.setApplicationName("Keyless Gaze Cursor")
    app = QApplication([sys.argv[0]])
    screens = QGuiApplication.screens()
    if not screens:
        return 1
    engine = CursorEngine(live_region=region, demo_only=args.demo)
    overlays = [CursorOverlay(screen=screen, engine=engine) for screen in screens]
    for overlay in overlays:
        overlay.show()
        overlay.raise_()

    def _watch() -> None:
        if args.parent_pid is not None and not _alive(args.parent_pid):
            app.quit()

    if args.parent_pid is not None:
        watchdog = QTimer(app)
        watchdog.setInterval(1000)
        watchdog.timeout.connect(_watch)
        watchdog.start()
    if args.duration is not None:
        QTimer.singleShot(max(0, int(args.duration * 1000)), app.quit)
    result = app.exec()
    engine.close()
    for overlay in overlays:
        overlay.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
