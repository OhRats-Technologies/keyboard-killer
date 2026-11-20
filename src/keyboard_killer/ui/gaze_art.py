"""Shared gaze-cursor geometry and the deterministic demo path."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

# The screen cursor can sit over arbitrary application colors, so its palette is
# intentionally fixed instead of following the app theme.
CURSOR_FILL = QColor("#141414")
CURSOR_OUTLINE = QColor("#f5f2e8")
CURSOR_RING = QColor("#c97b3f")


def demo_gaze(elapsed_s: float) -> tuple[float, float]:
    """Return a smooth, bounded demo position in normalized screen coordinates."""
    x = 0.5 + 0.36 * math.sin(elapsed_s * 0.43) * math.cos(elapsed_s * 0.17)
    y = 0.5 + 0.31 * math.sin(elapsed_s * 0.61 + 1.2)
    return (min(0.96, max(0.04, x)), min(0.94, max(0.06, y)))


def cursor_path() -> QPainterPath:
    """Classic pointer arrow with the hotspot at (0, 0)."""
    path = QPainterPath(QPointF(0.0, 0.0))
    for x, y in (
        (0.0, 25.0),
        (6.4, 18.6),
        (10.4, 27.0),
        (14.8, 25.0),
        (10.8, 16.8),
        (19.8, 16.0),
    ):
        path.lineTo(x, y)
    path.closeSubpath()
    return path


def cursor_bounds(scale: float = 1.0) -> QRectF:
    """Bounding rectangle including the halo ring and the demo label."""
    halo = 24.0 * scale
    return QRectF(-halo, -halo, halo * 2.0 + 80.0 * scale, halo * 2.0 + 30.0 * scale)


def draw_gaze_cursor(
    painter: QPainter,
    point: QPointF,
    *,
    scale: float = 1.0,
    label: str = "",
) -> None:
    """Draw the cursor and halo at ``point`` in widget coordinates."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(point)
    painter.scale(scale, scale)

    halo = QColor(CURSOR_RING)
    halo.setAlpha(58)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(halo, 1.8))
    painter.drawEllipse(QPointF(2.0, 5.0), 22.0, 22.0)
    halo.setAlpha(150)
    painter.setPen(QPen(halo, 1.6))
    painter.drawEllipse(QPointF(2.0, 5.0), 15.0, 15.0)

    arrow = cursor_path()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.save()
    painter.translate(1.4, 2.0)
    painter.drawPath(arrow)
    painter.restore()

    painter.setBrush(CURSOR_FILL)
    painter.setPen(
        QPen(
            CURSOR_OUTLINE,
            2.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.drawPath(arrow)

    if label:
        font = painter.font()
        font.setPointSizeF(7.5)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(label) + 12
        height = metrics.height() + 6
        bubble = QRectF(14.0, 24.0, width, height)
        painter.setPen(QPen(QColor(90, 68, 50, 180), 1.0))
        painter.setBrush(QColor(20, 20, 20, 214))
        painter.drawRect(bubble)
        painter.setPen(CURSOR_OUTLINE)
        painter.drawText(bubble, Qt.AlignmentFlag.AlignCenter, label)
    painter.restore()


__all__ = [
    "CURSOR_FILL",
    "CURSOR_OUTLINE",
    "CURSOR_RING",
    "cursor_bounds",
    "cursor_path",
    "demo_gaze",
    "draw_gaze_cursor",
]
