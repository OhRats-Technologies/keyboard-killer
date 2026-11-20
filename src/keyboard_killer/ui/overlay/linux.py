"""Linux overlay: click-through Qt window on X11, tray/toast fallback on Wayland (D3)."""

from __future__ import annotations

import os
from collections.abc import Mapping

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QLabel, QSystemTrayIcon, QWidget

from keyboard_killer.ui.overlay.base import (
    NullOverlay,
    Overlay,
    OverlayCapability,
    OverlayMode,
    OverlayState,
    OverlayUpdate,
)

STATE_COPY = {
    OverlayState.IDLE: "Idle",
    OverlayState.LISTENING: "Listening",
    OverlayState.PROCESSING: "Working",
    OverlayState.HEARD: "Heard",
    OverlayState.CLICKED: "Clicked",
    OverlayState.PERMISSION: "Needs permission",
    OverlayState.ERROR: "Problem",
}


def overlay_mode(env: Mapping[str, str] | None = None) -> OverlayCapability:
    """Return the honest D3 capability for this session without creating widgets."""
    environ = os.environ if env is None else env
    session = environ.get("XDG_SESSION_TYPE", "").lower()
    desktop = environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if session == "wayland":
        if "gnome" in desktop:
            reason = (
                "GNOME Wayland cannot make a normal window click-through. "
                "Using the tray/toast and Firefox HUD fallback."
            )
        else:
            reason = (
                "Wayland has no portable click-through API for a normal window. "
                "Using the tray/toast fallback."
            )
        return OverlayCapability(
            mode=OverlayMode.TRAY_TOAST,
            available=True,
            reason=reason,
            persistent_indicator=False,
        )
    if session == "x11":
        return OverlayCapability(
            mode=OverlayMode.TRANSPARENT_WINDOW,
            available=True,
            reason="X11 click-through overlay.",
            persistent_indicator=True,
        )
    return OverlayCapability(
        mode=OverlayMode.TRAY_TOAST,
        available=False,
        reason="No supported display session detected; use the main window.",
        persistent_indicator=False,
    )


class TrayToastOverlay(Overlay):
    """D3 v1 fallback: every state becomes a short tray notification."""

    def __init__(
        self,
        tray: QSystemTrayIcon | None = None,
        capability: OverlayCapability | None = None,
    ) -> None:
        self.tray = tray
        self._capability = capability or overlay_mode()

    def capability(self) -> OverlayCapability:
        return self._capability

    def show_state(self, update: OverlayUpdate) -> None:
        if self.tray is None:
            return
        title = STATE_COPY.get(update.state, "Keyboard-Killer")
        body = update.text or update.detail
        self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 2500)


class X11Overlay(Overlay):
    """A frameless, always-on-top, click-through status pill."""

    def __init__(self) -> None:
        self.widget = QWidget()
        self.widget.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.label = QLabel("Listening", self.widget)
        self.label.setStyleSheet(
            "background: #1a1a1b; color: #efede5; border: 1px solid #634936;"
            "padding: 6px 14px; font-family: 'Space Mono'; font-size: 11px;"
        )
        self.widget.resize(280, 40)
        self._timer = QTimer(self.widget)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def capability(self) -> OverlayCapability:
        return overlay_mode()

    def show_state(self, update: OverlayUpdate) -> None:
        text = STATE_COPY.get(update.state, "Status")
        detail = update.text or update.detail
        self.label.setText(f"{text}: {detail}" if detail else text)
        self.label.adjustSize()
        self.widget.resize(max(220, self.label.width() + 24), 40)
        screen = self.widget.screen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.widget.move(
                QPoint(geometry.x() + (geometry.width() - self.widget.width()) // 2, geometry.y() + 18)
            )
        self.widget.show()
        self._timer.start(3500)

    def hide(self) -> None:
        self.widget.hide()

    def close(self) -> None:
        self.widget.close()


def create_overlay(
    *,
    tray: QSystemTrayIcon | None = None,
    env: Mapping[str, str] | None = None,
) -> Overlay:
    capability = overlay_mode(env)
    if capability.mode is OverlayMode.TRANSPARENT_WINDOW:
        try:
            return X11Overlay()
        except Exception:
            return TrayToastOverlay(tray, capability)
    if capability.mode is OverlayMode.TRAY_TOAST:
        return TrayToastOverlay(tray, capability)
    return NullOverlay(capability.reason)
