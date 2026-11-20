"""Headless Qt fixtures. Set the offscreen platform before any PySide6 import."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from keyless.ui.theme.tokens import load_bundled_fonts

    load_bundled_fonts()
    app.setApplicationName("Keyless-Tests")
    yield app
    app.processEvents()
