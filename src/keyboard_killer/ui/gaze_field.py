"""Live gaze field: shared-memory gaze with an explicit demo fallback."""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from keyboard_killer.ui.gaze_art import demo_gaze, draw_gaze_cursor
from keyboard_killer.ui.gaze_source import GazeSample, LiveGaze

TICK_MS = 33
TRAIL_POINTS = 26


class GazeField(QWidget):
    """A screen map that follows live gaze, or a labeled demo path."""

    mode_changed = Signal(str)

    def __init__(self, window, *, source_size: tuple[int, int] | None = None) -> None:
        super().__init__()
        self.window = window
        self._source_width, self._source_height = source_size or (1920, 1080)
        try:
            monitors = tuple(
                (m.x, m.y, m.width, m.height) for m in window.display_layout.monitors
            )
        except Exception:
            monitors = ()
        self._monitors = monitors or ((0, 0, self._source_width, self._source_height),)
        self._bounds_x = min(m[0] for m in self._monitors)
        self._bounds_y = min(m[1] for m in self._monitors)
        self._bounds_w = max(1, max(m[0] + m[2] for m in self._monitors) - self._bounds_x)
        self._bounds_h = max(1, max(m[1] + m[3] for m in self._monitors) - self._bounds_y)
        self._gaze = LiveGaze()
        self._trail: deque[QPointF] = deque(maxlen=TRAIL_POINTS)
        self._started = time.monotonic()
        self._last_sample: GazeSample | None = None
        self._mode = "demo"
        self._frame = 0
        self.setMinimumSize(300, 150)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def mode(self) -> str:
        return self._mode

    def _tick(self) -> None:
        self._frame += 1
        sample = self._gaze.read()
        self._last_sample = sample
        if sample is not None and sample.live:
            mode = "live" if sample.face else "no-face"
        else:
            mode = "demo"
        if mode != self._mode:
            self._mode = mode
            self._trail.clear()
            self.mode_changed.emit(mode)
        self.update()

    def _screen_rect(self) -> QRectF:
        inset = QRectF(12.0, 30.0, max(1.0, self.width() - 24.0), max(1.0, self.height() - 54.0))
        aspect = self._bounds_w / self._bounds_h
        if inset.width() / inset.height() > aspect:
            width = inset.height() * aspect
            return QRectF(inset.center().x() - width / 2.0, inset.y(), width, inset.height())
        height = inset.width() / aspect
        return QRectF(inset.x(), inset.center().y() - height / 2.0, inset.width(), height)

    def _monitor_rects(self, screen: QRectF) -> list[QRectF]:
        rects: list[QRectF] = []
        for x, y, width, height in self._monitors:
            rects.append(
                QRectF(
                    screen.x() + (x - self._bounds_x) / self._bounds_w * screen.width(),
                    screen.y() + (y - self._bounds_y) / self._bounds_h * screen.height(),
                    width / self._bounds_w * screen.width(),
                    height / self._bounds_h * screen.height(),
                )
            )
        return rects

    def _field_point(self, screen: QRectF, x: float, y: float) -> QPointF:
        nx = min(0.995, max(0.005, (x - self._bounds_x) / self._bounds_w))
        ny = min(0.995, max(0.005, (y - self._bounds_y) / self._bounds_h))
        return QPointF(screen.x() + nx * screen.width(), screen.y() + ny * screen.height())

    def _position(self) -> tuple[QPointF, str]:
        screen = self._screen_rect()
        sample = self._last_sample
        if sample is not None and sample.live:
            return (
                self._field_point(screen, sample.x, sample.y),
                "live" if sample.face else "no-face",
            )
        nx, ny = demo_gaze(time.monotonic() - self._started)
        return (
            QPointF(
                screen.x() + nx * screen.width(),
                screen.y() + ny * screen.height(),
            ),
            "demo",
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        tokens = self.window.theme_tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(tokens.surface))
        screen = self._screen_rect()

        monitors = self._monitor_rects(screen)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.surface_alt))
        for rect in monitors:
            painter.drawRect(rect)

        grid = QColor(tokens.border)
        grid.setAlpha(28)
        painter.setPen(QPen(grid, 1))
        step = 24
        for x in range(int(screen.left()), int(screen.right()), step):
            painter.drawLine(x, int(screen.top()), x, int(screen.bottom()))
        for y in range(int(screen.top()), int(screen.bottom()), step):
            painter.drawLine(int(screen.left()), y, int(screen.right()), y)

        edge = QColor(tokens.border)
        edge.setAlpha(90)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge, 1))
        for rect in monitors:
            painter.drawRect(rect)

        point, mode = self._position()
        self._trail.append(point)
        if len(self._trail) > 1:
            for index in range(1, len(self._trail)):
                alpha = int(80 * index / len(self._trail))
                trail = QColor(tokens.muted)
                trail.setAlpha(alpha)
                painter.setPen(QPen(trail, 1.4))
                painter.drawLine(self._trail[index - 1], self._trail[index])

        draw_gaze_cursor(painter, point, scale=0.72)

        label_font = QFont("Space Mono")
        label_font.setPointSize(7)
        label_font.setBold(True)
        painter.setFont(label_font)
        painter.setPen(QColor(tokens.muted))
        painter.drawText(12, 18, "GAZE FIELD")

        badge = QColor(tokens.success) if mode == "live" else QColor(tokens.warning)
        painter.setPen(badge)
        badge_text = {
            "live": "LIVE",
            "no-face": "LIVE · NO FACE",
            "demo": "DEMO · NO VISION DATA",
        }[mode]
        painter.drawText(
            QRectF(0, 6, self.width() - 12, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            badge_text,
        )

        sample = self._last_sample
        if sample is not None and sample.live:
            detail = (
                f"X {sample.x:6.0f}  Y {sample.y:6.0f}  "
                f"{'BLINK' if sample.blinking else 'OPEN'}  {sample.age_s * 1000:4.0f} MS"
            )
        else:
            detail = "DEMO PATH · NO LIVE GAZE"
        painter.setPen(QColor(tokens.muted))
        painter.drawText(
            QRectF(12, self.height() - 18, self.width() - 24, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            detail,
        )
        painter.end()

    def shutdown(self) -> None:
        self._timer.stop()
        self._gaze.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
