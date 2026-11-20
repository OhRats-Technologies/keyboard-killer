"""OhRats UI-kit tokens for the desktop app.

The values mirror `OhRats-Technologies/ui/css/tokens.css`: warm neutrals, a
single brown border, sharp edges, Inter for prose, and Space Mono for labels,
numbers, and technical state. No widget owns a raw color.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from string import Template

FONT_DIR = Path(__file__).with_name("fonts")
CHEVRON = Path(__file__).with_name("chevron-down.svg")
_LOADED_FONTS = False


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
    radius: int = 0
    radius_sm: int = 0
    font_family: str = 'Inter, "Noto Sans", "DejaVu Sans", sans-serif'
    mono_family: str = '"Space Mono", "MesloLGS Nerd Font Mono", monospace'
    font_size: int = 14
    title_size: int = 30
    heading_size: int = 18
    pill_height: int = 32

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


LIGHT = ThemeTokens(
    name=ThemeName.LIGHT,
    background="#efede5",
    surface="#ffffff",
    surface_alt="#f5f5f5",
    sidebar="#e8e5dc",
    text="#1a1a1b",
    muted="#595959",
    accent="#1a1a1b",
    accent_text="#efede5",
    border="#634936",
    danger="#d95555",
    success="#2f9e62",
    warning="#a36a00",
    selected="#dcd8ce",
)

DARK = ThemeTokens(
    name=ThemeName.DARK,
    background="#1a1a1b",
    surface="#202021",
    surface_alt="#2a2a2b",
    sidebar="#151516",
    text="#efede5",
    muted="#a3a393",
    accent="#efede5",
    accent_text="#1a1a1b",
    border="#634936",
    danger="#e26464",
    success="#43b777",
    warning="#e2b866",
    selected="#2a2a2b",
)


def tokens_for(name: ThemeName | str) -> ThemeTokens:
    resolved = ThemeName(name) if isinstance(name, str) else name
    return DARK if resolved is ThemeName.DARK else LIGHT


def load_bundled_fonts() -> bool:
    """Register Inter and Space Mono with Qt; safe to call repeatedly."""
    global _LOADED_FONTS
    if _LOADED_FONTS:
        return True
    try:
        from PySide6.QtGui import QFontDatabase

        loaded = False
        for path in FONT_DIR.glob("*.ttf"):
            if QFontDatabase.addApplicationFont(str(path)) >= 0:
                loaded = True
        _LOADED_FONTS = loaded
        return loaded
    except Exception:
        return False


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
        "mono_family": tokens.mono_family,
        "font_size": f"{tokens.font_size}px",
        "title_size": f"{tokens.title_size}px",
        "heading_size": f"{tokens.heading_size}px",
        "pill_height": f"{tokens.pill_height}px",
        "pill_radius": f"{tokens.pill_height // 2}px",
        "chevron": str(CHEVRON),
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
    QLabel {
        background: transparent;
    }
    QCheckBox {
        background: transparent;
    }
    QWidget#Content {
        background: $background;
    }
    QFrame#Sidebar {
        background: $sidebar;
        border-right: 1px solid $border;
    }
    QLabel#Wordmark {
        color: $text;
        font-family: $mono_family;
        font-size: 13px;
        font-weight: 700;
    }
    QLabel#Title {
        color: $text;
        font-size: $title_size;
        font-weight: 500;
    }
    QLabel#Heading {
        color: $text;
        font-size: $heading_size;
        font-weight: 500;
    }
    QLabel#Mono, QLabel#Eyebrow {
        color: $muted;
        font-family: $mono_family;
        font-size: 11px;
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
    QFrame#Divider {
        background: $border;
        max-height: 1px;
        min-height: 1px;
    }
    QFrame#Pill, QLabel#Pill {
        background: $surface;
        border: 1px solid $border;
        border-radius: 0px;
        color: $muted;
        font-family: $mono_family;
        font-size: 10px;
        min-height: $pill_height;
        padding: 0 $spacing_md;
    }
    QPushButton {
        background: transparent;
        color: $text;
        border: 1px solid $border;
        border-radius: 0px;
        padding: $spacing_sm $spacing_md;
        min-height: 32px;
        font-family: $mono_family;
        font-size: 11px;
        font-weight: 700;
    }
    QPushButton:hover {
        background: $surface_alt;
        border-color: $text;
    }
    QPushButton:focus {
        border: 1px solid $text;
    }
    QPushButton:disabled {
        color: $muted;
        border-color: $border;
    }
    QPushButton#Primary {
        background: $text;
        color: $background;
        border: 1px solid $text;
    }
    QPushButton#Primary:hover {
        background: $text;
        color: $background;
        border-color: $muted;
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
        padding: $spacing_sm 0;
        font-family: $mono_family;
        font-size: 11px;
        font-weight: 700;
    }
    QListWidget#Nav::item {
        padding: $spacing_sm $spacing_md;
        border-left: 2px solid transparent;
        margin: 0;
    }
    QListWidget#Nav::item:hover {
        background: $surface_alt;
    }
    QListWidget#Nav::item:selected {
        background: $selected;
        color: $text;
        border-left: 2px solid $text;
    }
    QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
        background: $surface;
        color: $text;
        border: 1px solid $border;
        border-radius: 0px;
        padding: 6px $spacing_sm;
        min-height: 24px;
        selection-background-color: $text;
        selection-color: $background;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {
        border: 1px solid $text;
    }
    QComboBox::drop-down {
        border-left: 1px solid $border;
        width: 24px;
    }
    QComboBox::down-arrow {
        image: url("$chevron");
        width: 10px;
        height: 6px;
    }
    QComboBox QAbstractItemView {
        background: $surface;
        color: $text;
        border: 1px solid $border;
        selection-background-color: $text;
        selection-color: $background;
    }
    QCheckBox {
        spacing: $spacing_sm;
        color: $text;
    }
    QProgressBar {
        background: $background;
        border: 1px solid $border;
        border-radius: 0px;
        color: $text;
        font-family: $mono_family;
        font-size: 10px;
        text-align: center;
        min-height: 20px;
    }
    QProgressBar::chunk {
        background: $text;
        border-radius: 0px;
    }
    QTableWidget {
        background: $surface;
        color: $text;
        gridline-color: $border;
        border: 1px solid $border;
        border-radius: 0px;
        selection-background-color: $text;
        selection-color: $background;
    }
    QTableWidget::item {
        padding: $spacing_xs;
    }
    QHeaderView::section {
        background: $background;
        color: $muted;
        border: none;
        border-bottom: 1px solid $border;
        padding: 6px;
        font-family: $mono_family;
        font-size: 10px;
        font-weight: 700;
    }
    QScrollArea {
        border: none;
    }
    QScrollBar:vertical {
        background: $background;
        width: 10px;
        margin: 0;
    }
    QScrollBar::handle:vertical {
        background: $border;
        min-height: 24px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
    }
    QStatusBar {
        background: $sidebar;
        color: $muted;
        border-top: 1px solid $border;
        font-family: $mono_family;
        font-size: 10px;
    }
    QMenu {
        background: $surface;
        color: $text;
        border: 1px solid $border;
    }
    QMenu::item {
        padding: 6px 18px;
    }
    QMenu::item:selected {
        background: $text;
        color: $background;
    }
    QToolTip {
        background: $surface;
        color: $text;
        border: 1px solid $border;
    }
"""
