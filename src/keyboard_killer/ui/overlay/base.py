"""Overlay contract and shared state model (D3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OverlayState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    HEARD = "heard"
    CLICKED = "clicked"
    PERMISSION = "permission"
    ERROR = "error"


class OverlayMode(str, Enum):
    TRANSPARENT_WINDOW = "transparent_window"
    TRAY_TOAST = "tray_toast"
    NONE = "none"


@dataclass(frozen=True)
class OverlayCapability:
    mode: OverlayMode
    available: bool
    reason: str = ""
    persistent_indicator: bool = False


@dataclass
class OverlayUpdate:
    state: OverlayState
    text: str = ""
    detail: str = ""


class Overlay:
    """Never takes focus; only the pill controls accept clicks."""

    def capability(self) -> OverlayCapability:
        raise NotImplementedError

    def show_state(self, update: OverlayUpdate) -> None:
        raise NotImplementedError

    def show_target(self, x: float, y: float, label: str = "") -> None:
        return None

    def hide(self) -> None:
        return None

    def close(self) -> None:
        return None
