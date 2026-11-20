"""Standalone click-through gaze cursor overlay for X11 and XWayland.

Wayland does not let a normal client place a top-level window at an arbitrary
screen position, so the main app launches this module with Qt's ``xcb`` plugin
whenever an X display is available.  The process reads the daemon's shared
gaze snapshot and falls back to a clearly labeled demo path when no live state
is published.

One managed, frameless window covers each monitor. KWin keeps managed XWayland
windows above Wayland windows; a single virtual-desktop-sized override-redirect
window is not reliably composited above native Wayland clients.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from PySide6.QtCore import QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from keyboard_killer.platform.linux.display import get_display_layout
from keyboard_killer.ui.gaze_art import cursor_bounds, demo_gaze, draw_gaze_cursor
from keyboard_killer.ui.gaze_source import LiveGaze

TICK_MS = 16
DEMO_LABEL = "DEMO"
CURSOR_SCALE = 1.45


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


class CursorOverlay(QWidget):
    """One transparent surface per monitor; paints only near the cursor."""

    def __init__(
        self,
        *,
        screen: QScreen,
        virtual_geometry: QRect,
        started: float,
        demo_only: bool = False,
        source_size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self.demo_only = demo_only
        self.window_geometry = screen.availableGeometry()
        self.virtual_geometry = virtual_geometry
        self._started = started
        self.source_width, self.source_height = source_size or (1920, 1080)
        self._gaze = LiveGaze()
        self._last_point: QPointF | None = None
        self._mode = "demo"

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
        self.setWindowTitle(f"Keyboard-Killer Gaze Cursor ({screen.name()})")

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- state ---------------------------------------------------------
    def _sample(self) -> tuple[float, float, str]:
        elapsed = time.monotonic() - self._started
        if not self.demo_only:
            sample = self._gaze.read()
            if sample is not None and sample.live:
                x = sample.x / max(1, self.source_width)
                y = sample.y / max(1, self.source_height)
                return (
                    min(0.995, max(0.005, x)),
                    min(0.995, max(0.005, y)),
                    "live" if sample.face else "no-face",
                )
        x, y = demo_gaze(elapsed)
        return x, y, "demo"

    def _tick(self) -> None:
        nx, ny, mode = self._sample()
        if mode != self._mode:
            self._mode = mode

        global_point = QPointF(
            self.virtual_geometry.x() + nx * self.virtual_geometry.width(),
            self.virtual_geometry.y() + ny * self.virtual_geometry.height(),
        )
        local = global_point - QPointF(self.window_geometry.topLeft())
        if 0 <= local.x() <= self.width() and 0 <= local.y() <= self.height():
            label = self._current_label()
            if self._last_point is None:
                self.update(self._cursor_rect(local, label))
            else:
                old = self._cursor_rect(self._last_point, self._current_label())
                new = self._cursor_rect(local, label)
                self.update(old.united(new))
            self._last_point = local
        elif self._last_point is not None:
            self.update(self._cursor_rect(self._last_point, self._current_label()))
            self._last_point = None

    def _current_label(self) -> str:
        if self._mode == "no-face":
            return "NO FACE"
        if self._mode == "demo":
            return DEMO_LABEL
        return ""

    def _cursor_rect(self, point: QPointF, label: str) -> QRect:
        bounds = cursor_bounds(CURSOR_SCALE)
        rect = QRect(
            int(point.x() + bounds.left()) - 8,
            int(point.y() + bounds.top()) - 8,
            int(bounds.width()) + 16,
            int(bounds.height()) + 16,
        )
        if label:
            rect = rect.united(QRect(int(point.x()) + 8, int(point.y()) + 20, 90, 30))
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
            label=self._current_label(),
        )
        painter.end()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._gaze.close()
        self._timer.stop()
        super().closeEvent(event)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keyboard-Killer gaze cursor overlay")
    parser.add_argument("--demo", action="store_true", help="ignore live gaze and animate a demo")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process exits")
    parser.add_argument("--duration", type=float, default=None, help="exit after N seconds")
    parser.add_argument("--source-width", type=int, default=None)
    parser.add_argument("--source-height", type=int, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    source = None
    if args.source_width and args.source_height:
        source = (args.source_width, args.source_height)
    else:
        try:
            layout = get_display_layout()
            source = (layout.total_width, layout.total_height)
        except Exception:
            source = (1920, 1080)

    QApplication.setApplicationName("Keyboard-Killer Gaze Cursor")
    app = QApplication([sys.argv[0]])
    screens = QGuiApplication.screens()
    if not screens:
        return 1
    virtual = screens[0].virtualGeometry()
    started = time.monotonic()
    overlays = [
        CursorOverlay(
            screen=screen,
            virtual_geometry=virtual,
            started=started,
            demo_only=args.demo,
            source_size=source,
        )
        for screen in screens
    ]
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
    for overlay in overlays:
        overlay.close()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
