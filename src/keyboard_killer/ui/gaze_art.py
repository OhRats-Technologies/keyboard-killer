"""Shared gaze-cursor geometry and the deterministic demo path."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QRadialGradient,
)

# The screen cursor can sit over arbitrary application colors, so its palette is
# intentionally fixed instead of following the app theme.
CURSOR_TINT = QColor("#c97b3f")
CURSOR_LABEL = QColor("#f5f2e8")
CURSOR_HOLE = 0.16


def demo_gaze(elapsed_s: float) -> tuple[float, float]:
    """Return a smooth, bounded demo position in normalized screen coordinates."""
    x = 0.5 + 0.36 * math.sin(elapsed_s * 0.43) * math.cos(elapsed_s * 0.17)
    y = 0.5 + 0.31 * math.sin(elapsed_s * 0.61 + 1.2)
    return (min(0.96, max(0.04, x)), min(0.94, max(0.06, y)))


def cursor_bounds(scale: float = 1.0) -> QRectF:
    """Bounding rectangle including the soft ring and the mode label."""
    radius = 52.0 * scale
    return QRectF(-radius, -radius, radius * 2.0, radius * 2.0 + 30.0 * scale)


def draw_gaze_cursor(
    painter: QPainter,
    point: QPointF,
    *,
    scale: float = 1.0,
    label: str = "",
) -> None:
    """Draw a soft ring with a fully transparent center at ``point``."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(point)
    painter.scale(scale, scale)

    radius = 48.0
    tint = QColor(CURSOR_TINT)

    # A gentle wash that fades out toward both the hole and the rim.
    gradient = QRadialGradient(QPointF(0.0, 0.0), radius)
    gradient.setColorAt(0.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
    gradient.setColorAt(CURSOR_HOLE, QColor(tint.red(), tint.green(), tint.blue(), 0))
    gradient.setColorAt(0.26, QColor(tint.red(), tint.green(), tint.blue(), 24))
    gradient.setColorAt(0.52, QColor(tint.red(), tint.green(), tint.blue(), 62))
    gradient.setColorAt(0.68, QColor(tint.red(), tint.green(), tint.blue(), 78))
    gradient.setColorAt(0.84, QColor(tint.red(), tint.green(), tint.blue(), 28))
    gradient.setColorAt(1.0, QColor(tint.red(), tint.green(), tint.blue(), 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawEllipse(QPointF(0.0, 0.0), radius, radius)

    # Thin definition rings keep the shape readable on busy backgrounds.
    ring = QColor(tint)
    ring.setAlpha(145)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(ring, 1.2))
    painter.drawEllipse(QPointF(0.0, 0.0), 31.0, 31.0)

    faint = QColor(tint)
    faint.setAlpha(34)
    painter.setPen(QPen(faint, 1.0))
    painter.drawEllipse(QPointF(0.0, 0.0), 11.0, 11.0)

    if label:
        font = painter.font()
        font.setPointSizeF(7.5)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 12
        height = metrics.height() + 6
        bubble = QRectF(-width / 2.0, 43.0, width, height)
        painter.setPen(QPen(QColor(90, 68, 50, 160), 1.0))
        painter.setBrush(QColor(20, 20, 20, 196))
        painter.drawRect(bubble)
        painter.setPen(CURSOR_LABEL)
        painter.drawText(bubble, Qt.AlignmentFlag.AlignCenter, label)
    painter.restore()


__all__ = [
    "CURSOR_TINT",
    "cursor_bounds",
    "demo_gaze",
    "draw_gaze_cursor",
]
