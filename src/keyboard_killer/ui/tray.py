"""Tray/menu-bar surface shared by the window and the D3 fallback."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from keyboard_killer.ui.session import SessionState


def make_icon(background: str = "#19191a", foreground: str = "#f4f2ec") -> QIcon:
    """Crisp rounded monochrome K mark shared by the window and system tray."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(background))
    painter.setPen(QPen(QColor("#3a3a3e"), 2))
    painter.drawRoundedRect(QRect(4, 4, 56, 56), 14, 14)
    painter.setPen(QColor(foreground))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(30)
    painter.setFont(font)
    painter.drawText(QRect(4, 4, 56, 56), Qt.AlignmentFlag.AlignCenter, "K")
    painter.end()
    return QIcon(pixmap)


class Tray:
    """One tray icon; the menu mirrors Listening/Paused, Open, Pause, Quit."""

    def __init__(
        self,
        *,
        window: object | None = None,
        on_open: Callable[[], None] | None = None,
        on_pause: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_calibrate: Callable[[], None] | None = None,
    ) -> None:
        self.icon = QSystemTrayIcon(make_icon())
        self.icon.setToolTip("Keyboard-Killer: stopped")
        self.menu = QMenu()
        self.open_action = QAction("Open Keyboard-Killer", self.menu)
        self.pause_action = QAction("Pause", self.menu)
        self.calibrate_action = QAction("Calibrate", self.menu)
        self.quit_action = QAction("Quit", self.menu)
        self.open_action.triggered.connect(on_open or (lambda: None))
        self.pause_action.triggered.connect(on_pause or (lambda: None))
        self.calibrate_action.triggered.connect(on_calibrate or (lambda: None))
        self.quit_action.triggered.connect(on_quit or (lambda: None))
        self.menu.addAction(self.open_action)
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()
        self.menu.addAction(self.calibrate_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.icon.setContextMenu(self.menu)
        self._window = window

    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def start(self) -> bool:
        if not self.available():
            return False
        self.icon.show()
        self.icon.activated.connect(self._activated)
        return True

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ) and self._window is not None:
            show = getattr(self._window, "show", None)
            if callable(show):
                show()

    def set_state(self, state: SessionState, detail: str = "") -> None:
        labels = {
            SessionState.STOPPED: "stopped",
            SessionState.LISTENING: "listening",
            SessionState.PAUSED: "paused",
            SessionState.ERROR: "error",
        }
        self.icon.setToolTip(f"Keyboard-Killer: {labels.get(state, state.value)}")
        self.pause_action.setText("Resume" if state is SessionState.PAUSED else "Pause")

    def stop(self) -> None:
        self.icon.hide()


def tray_supported() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return QSystemTrayIcon.isSystemTrayAvailable()
