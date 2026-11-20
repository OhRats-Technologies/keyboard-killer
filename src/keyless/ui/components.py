"""Small shared surfaces and calm controls for the OhRats desktop kit."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """A clean bordered surface with generous padding and a vertical layout."""

    def __init__(self, parent: QWidget | None = None, *, subtle: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("SubtleCard" if subtle else "Card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 18, 20, 18)
        self.layout.setSpacing(12)


def eyebrow(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def status(text: str, ok: bool | None = None) -> QLabel:
    label = QLabel(text)
    if ok is True:
        label.setObjectName("Success")
    elif ok is False:
        label.setObjectName("Warning")
    else:
        label.setObjectName("Muted")
    return label


def mono(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Mono")
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    return line


_FRIENDLY_KEYS: dict[str, str] = {
    "KEY_RIGHTCTRL": "Right Control",
    "KEY_LEFTCTRL": "Left Control",
    "KEY_RIGHTALT": "Right Alt",
    "KEY_LEFTALT": "Left Alt",
    "KEY_RIGHTSHIFT": "Right Shift",
    "KEY_LEFTSHIFT": "Left Shift",
    "KEY_CAPSLOCK": "Caps Lock",
    "KEY_SPACE": "Space",
    "KEY_SCROLLLOCK": "Scroll Lock",
    "KEY_PAUSE": "Pause",
}

_QT_TO_EVDEV: dict[int, str] = {
    int(Qt.Key.Key_Control): "KEY_RIGHTCTRL",
    int(Qt.Key.Key_Alt): "KEY_RIGHTALT",
    int(Qt.Key.Key_Shift): "KEY_RIGHTSHIFT",
    int(Qt.Key.Key_CapsLock): "KEY_CAPSLOCK",
    int(Qt.Key.Key_Space): "KEY_SPACE",
    int(Qt.Key.Key_ScrollLock): "KEY_SCROLLLOCK",
    int(Qt.Key.Key_Pause): "KEY_PAUSE",
    **{int(getattr(Qt.Key, f"Key_F{n}")): f"KEY_F{n}" for n in range(1, 13)},
}


def friendly_key_name(code: str) -> str:
    """Display a Linux evdev keycode (e.g. KEY_RIGHTCTRL) as a human shortcut label."""
    norm = (code or "").strip().upper()
    if norm in _FRIENDLY_KEYS:
        return _FRIENDLY_KEYS[norm]
    if norm.startswith("KEY_"):
        return norm[4:].replace("_", " ").title()
    return norm or "Right Control"


class KeyCaptureButton(QPushButton):
    """Click to record a push-to-talk key; displays a friendly name while storing evdev code."""

    key_changed = Signal(str)

    def __init__(self, key_code: str = "KEY_RIGHTCTRL", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("KeyCap")
        self._code = key_code or "KEY_RIGHTCTRL"
        self._listening = False
        self.clicked.connect(self._begin_capture)
        self._sync_label()

    def text_code(self) -> str:
        return self._code

    def text(self) -> str:  # Compatibility for tests calling .hotkey.text()
        return self._code

    def setText(self, code: str) -> None:  # noqa: N802 - compatibility for .hotkey.setText("KEY_F9")
        self._code = (code or "KEY_RIGHTCTRL").strip()
        self._listening = False
        self._sync_label()
        self.key_changed.emit(self._code)

    def _sync_label(self) -> None:
        super().setText("Press any key…" if self._listening else friendly_key_name(self._code))

    def _begin_capture(self) -> None:
        self._listening = True
        self._sync_label()
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._listening:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key.Key_Escape:
            self._listening = False
            self._sync_label()
            return
        mapped = _QT_TO_EVDEV.get(int(event.key()))
        if mapped is None:
            txt = event.text().strip().upper()
            if len(txt) == 1 and txt.isalnum():
                mapped = f"KEY_{txt}"
        if mapped:
            self.setText(mapped)
        else:
            self._listening = False
            self._sync_label()


class DisclosureSection(QWidget):
    """Collapsible advanced section hidden by default to keep primary screens calm."""

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        on_expand: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._on_expand = on_expand
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(8)
        self.toggle_button = QPushButton(f"▸  {title}")
        self.toggle_button.setObjectName("Ghost")
        self.toggle_button.setCheckable(True)
        self.toggle_button.toggled.connect(self._on_toggled)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.toggle_button)
        header.addStretch(1)
        root.addLayout(header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.body.setVisible(False)
        root.addWidget(self.body)

    def _on_toggled(self, expanded: bool) -> None:
        self.toggle_button.setText(f"{'▾' if expanded else '▸'}  {self._title}")
        self.body.setVisible(expanded)
        if expanded and self._on_expand is not None:
            self._on_expand()
