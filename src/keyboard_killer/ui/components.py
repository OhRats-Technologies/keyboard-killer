"""Small shared surfaces for the OhRats desktop kit."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Card(QFrame):
    """A sharp, bordered surface with a vertical content layout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(20, 18, 20, 18)
        self.layout.setSpacing(10)


def eyebrow(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("Eyebrow")
    return label


def status(text: str, ok: bool | None = None) -> QLabel:
    label = QLabel(text.upper())
    if ok is True:
        label.setObjectName("Success")
    elif ok is False:
        label.setObjectName("Warning")
    else:
        label.setObjectName("Muted")
    label.setAlignment(label.alignment().__class__())
    return label


def mono(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Mono")
    return label
