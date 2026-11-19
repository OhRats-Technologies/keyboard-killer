"""Token-based theme (colors, spacing, type) for every UI surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from string import Template


class ThemeName(str, Enum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class ThemeTokens:
    name: ThemeName
    background: str
    surface: str
    surface_alt: str
    sidebar: str
    text: str
    muted: str
    accent: str
    accent_text: str
    border: str
    danger: str
    success: str
    warning: str
    selected: str
    spacing_xs: int = 4
    spacing_sm: int = 8
    spacing_md: int = 16
    spacing_lg: int = 24
    radius: int = 10
    radius_sm: int = 6
    font_family: str = "Noto Sans, DejaVu Sans, sans-serif"
    font_size: int = 13
    title_size: int = 22
    heading_size: int = 16
    pill_height: int = 30

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    background="#f7f7f9",
    surface="#ffffff",
    surface_alt="#eef0f4",
    sidebar="#f0f1f5",
    text="#1b1d22",
    muted="#5f6673",
    accent="#2f6fed",
    accent_text="#ffffff",
    border="#d9dce3",
    danger="#b3261e",
    success="#1f7a4d",
    warning="#8a5a00",
    selected="#e3ebfd",
)

DARK = ThemeTokens(
    name=ThemeName.DARK,
    background="#17191d",
    surface="#202329",
    surface_alt="#2a2e36",
    sidebar="#1c1f24",
    text="#f0f2f5",
    muted="#a8afbb",
    accent="#7aa2ff",
    accent_text="#0b1020",
    border="#363b45",
    danger="#ff8a80",
    success="#6fd39b",
    warning="#f0c66a",
    selected="#2c3b5e",
)


def tokens_for(name: ThemeName | str) -> ThemeTokens:
    resolved = ThemeName(name) if isinstance(name, str) else name
    return DARK if resolved is ThemeName.DARK else LIGHT


def system_theme_name(app: object | None = None) -> ThemeName:
    """Follow the system preference when Qt exposes one; default to light."""
    try:
        from PySide6.QtCore import Qt

        hints = app.styleHints() if app is not None else None
        if hints is not None:
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return ThemeName.DARK
    except Exception:
        pass
    return ThemeName.LIGHT


def stylesheet(tokens: ThemeTokens) -> str:
    values = {
        "background": tokens.background,
        "surface": tokens.surface,
        "surface_alt": tokens.surface_alt,
        "sidebar": tokens.sidebar,
        "text": tokens.text,
        "muted": tokens.muted,
        "accent": tokens.accent,
        "accent_text": tokens.accent_text,
        "border": tokens.border,
        "danger": tokens.danger,
        "success": tokens.success,
        "warning": tokens.warning,
        "selected": tokens.selected,
        "spacing_xs": f"{tokens.spacing_xs}px",
        "spacing_sm": f"{tokens.spacing_sm}px",
        "spacing_md": f"{tokens.spacing_md}px",
        "spacing_lg": f"{tokens.spacing_lg}px",
        "radius": f"{tokens.radius}px",
        "radius_sm": f"{tokens.radius_sm}px",
        "font_family": tokens.font_family,
        "font_size": f"{tokens.font_size}px",
        "title_size": f"{tokens.title_size}px",
        "heading_size": f"{tokens.heading_size}px",
        "pill_height": f"{tokens.pill_height}px",
        "pill_radius": f"{tokens.pill_height // 2}px",
    }
    return Template(_QSS).substitute(values)


_QSS = """
    QWidget {
        background: $background;
        color: $text;
        font-family: $font_family;
        font-size: $font_size;
    }
    QMainWindow, QDialog {
        background: $background;
    }
    QFrame#Sidebar {
        background: $sidebar;
        border-right: 1px solid $border;
    }
    QLabel#Title {
        font-size: $title_size;
        font-weight: 600;
    }
    QLabel#Heading {
        font-size: $heading_size;
        font-weight: 600;
    }
    QLabel#Muted {
        color: $muted;
    }
    QLabel#Success {
        color: $success;
    }
    QLabel#Danger {
        color: $danger;
    }
    QLabel#Warning {
        color: $warning;
    }
    QFrame#Card {
        background: $surface;
        border: 1px solid $border;
        border-radius: $radius;
    }
    QFrame#Pill, QLabel#Pill {
        background: $surface_alt;
        border: 1px solid $border;
        border-radius: $pill_radius;
        min-height: $pill_height;
        padding: 0 $spacing_md;
    }
    QPushButton {
        background: $surface;
        color: $text;
        border: 1px solid $border;
        border-radius: $radius_sm;
        padding: $spacing_sm $spacing_md;
        min-height: 28px;
    }
    QPushButton:hover {
        background: $surface_alt;
    }
    QPushButton:focus {
        border: 2px solid $accent;
    }
    QPushButton#Primary {
        background: $accent;
        color: $accent_text;
        border: 1px solid $accent;
        font-weight: 600;
    }
    QPushButton#Primary:disabled {
        background: $surface_alt;
        color: $muted;
        border: 1px solid $border;
    }
    QPushButton#Destructive {
        color: $danger;
        border: 1px solid $danger;
        background: transparent;
    }
    QListWidget#Nav {
        background: $sidebar;
        border: none;
        outline: none;
        padding: $spacing_sm;
    }
    QListWidget#Nav::item {
        padding: $spacing_sm $spacing_md;
        border-radius: $radius_sm;
        margin: 2px 0;
    }
    QListWidget#Nav::item:selected {
        background: $selected;
        color: $text;
    }
    QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
        background: $surface;
        color: $text;
        border: 1px solid $border;
        border-radius: $radius_sm;
        padding: 6px $spacing_sm;
        min-height: 24px;
    }
    QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
        border: 2px solid $accent;
    }
    QCheckBox {
        spacing: $spacing_sm;
    }
    QProgressBar {
        background: $surface_alt;
        border: 1px solid $border;
        border-radius: $radius_sm;
        text-align: center;
        min-height: 18px;
    }
    QProgressBar::chunk {
        background: $accent;
        border-radius: $radius_sm;
    }
    QTableWidget {
        background: $surface;
        gridline-color: $border;
        border: 1px solid $border;
        border-radius: $radius_sm;
    }
    QHeaderView::section {
        background: $surface_alt;
        color: $text;
        border: none;
        padding: 6px;
    }
    QScrollArea {
        border: none;
    }
    QStatusBar {
        background: $sidebar;
        color: $muted;
        border-top: 1px solid $border;
    }
    QToolTip {
        background: $surface;
        color: $text;
        border: 1px solid $border;
    }
"""
