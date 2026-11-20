"""Live gaze field: shared-memory gaze with an explicit demo fallback."""

from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from keyless.ui.cursor_motion import GazeMotion, MotionSettings
from keyless.ui.gaze_art import draw_gaze_cursor
from keyless.ui.gaze_region import GazeRegion, calibration_region
from keyless.ui.gaze_source import GazeSample, LiveGaze

TICK_MS = 33
TRAIL_POINTS = 26


class GazeField(QWidget):
    """A screen map that follows live gaze, or a labeled demo path."""

    mode_changed = Signal(str)

    def __init__(
        self,
        window,
        *,
        source_size: tuple[int, int] | None = None,
        live_region: GazeRegion | None = None,
    ) -> None:
        super().__init__()
        self.window = window
        self._source_width, self._source_height = source_size or (1920, 1080)
        try:
            layout = window.display_layout
            monitors = tuple((m.x, m.y, m.width, m.height) for m in layout.monitors)
        except Exception:
            layout = None
            monitors = ()
        self._monitors = monitors or ((0, 0, self._source_width, self._source_height),)
        self._bounds_x = min(m[0] for m in self._monitors)
        self._bounds_y = min(m[1] for m in self._monitors)
        self._bounds_w = max(1, max(m[0] + m[2] for m in self._monitors) - self._bounds_x)
        self._bounds_h = max(1, max(m[1] + m[3] for m in self._monitors) - self._bounds_y)
        if live_region is None:
            live_region = (
                calibration_region(window.paths.calibration_path, layout)
                if layout is not None
                else None
            )
        self._live_region = live_region
        self._motion = GazeMotion(
            MotionSettings(
                stiffness=140.0,
                fixation_radius=26.0,
                fixation_break_radius=48.0,
                search_spread=95.0,
            )
        )
        self._point = QPointF()
        self._last_tick = 0.0
        self._gaze = LiveGaze()
        self._trail: deque[QPointF] = deque(maxlen=TRAIL_POINTS)
        self._started = time.monotonic()
        self._last_sample: GazeSample | None = None
        self._mode = "idle"
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

    def _live_field_rect(self, screen: QRectF) -> QRectF:
        region = self._live_region
        if region is None:
            return screen
        return QRectF(
            screen.x() + (region.x - self._bounds_x) / self._bounds_w * screen.width(),
            screen.y() + (region.y - self._bounds_y) / self._bounds_h * screen.height(),
            region.width / self._bounds_w * screen.width(),
            region.height / self._bounds_h * screen.height(),
        )

    def _tick(self) -> None:
        self._frame += 1
        now = time.monotonic()
        dt = now - self._last_tick if self._last_tick > 0.0 else TICK_MS / 1000.0
        self._last_tick = now
        sample = self._gaze.read()
        self._last_sample = sample
        screen = self._screen_rect()
        live_rect = self._live_field_rect(screen)
        region = self._live_region

        if sample is not None and sample.live:
            mode = "live" if sample.face else "no-face"
            if region is not None:
                source_w = max(1.0, region.source_width or region.width)
                source_h = max(1.0, region.source_height or region.height)
                nx = min(1.0, max(0.0, (sample.x - region.source_x) / source_w))
                ny = min(1.0, max(0.0, (sample.y - region.source_y) / source_h))
                target = QPointF(
                    live_rect.x() + nx * live_rect.width(),
                    live_rect.y() + ny * live_rect.height(),
                )
            else:
                target = self._field_point(screen, sample.x, sample.y)
        else:
            mode = "idle"
            target = screen.center()

        if mode != self._mode:
            self._mode = mode
            self._motion.reset(target.x(), target.y(), now)
            self._trail.clear()
            self.mode_changed.emit(mode)

        if mode == "idle":
            self._point = target
        else:
            mx, my, _ = self._motion.update(
                target.x(),
                target.y(),
                dt,
                active=sample.face,
                blinking=sample.blinking,
            )
            self._point = QPointF(mx, my)
            self._trail.append(self._point)
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
            painter.drawRoundedRect(rect, 6.0, 6.0)

        edge = QColor(tokens.border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge, 1.0))
        for rect in monitors:
            painter.drawRoundedRect(rect, 6.0, 6.0)

        point, mode = self._point, self._mode
        if mode != "idle":
            if len(self._trail) > 1:
                for index in range(1, len(self._trail)):
                    alpha = int(75 * index / len(self._trail))
                    trail = QColor(tokens.muted)
                    trail.setAlpha(alpha)
                    painter.setPen(QPen(trail, 1.4))
                    painter.drawLine(self._trail[index - 1], self._trail[index])
            draw_gaze_cursor(painter, point, scale=0.45)
        else:
            # Calm resting state when engine is stopped or paused.
            center = screen.center()
            ring = QColor(tokens.muted)
            ring.setAlpha(90)
            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 7.0, 7.0)

        label_font = QFont("Inter")
        label_font.setPointSize(9)
        label_font.setWeight(QFont.Weight.Medium)
        painter.setFont(label_font)
        badge_color = QColor(tokens.success) if mode == "live" else QColor(tokens.muted)
        painter.setPen(badge_color)
        badge_text = {
            "live": "● Live",
            "no-face": "No face in view",
            "idle": "Idle",
        }.get(mode, "Idle")
        painter.drawText(
            QRectF(12, 6, self.width() - 24, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            badge_text,
        )
        painter.end()

    def shutdown(self) -> None:
        self._timer.stop()
        self._gaze.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.shutdown()
        super().closeEvent(event)
