"""Tests for t3code-style single-instance activation and user-centric error reporting."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QGuiApplication

from keyless.config.paths import HOME_ENV, default_paths
from keyless.config.schema import AppConfig
from keyless.ui.app import MainWindow
from keyless.ui.instance_lock import SingleInstanceServer, try_activate_existing
from keyless.ui.session import NullController, SessionState, SessionStatus, inspect_daemon_error
from keyless.ui.theme.tokens import ThemeName


def test_single_instance_activation_focuses_existing_window(qapp, tmp_path) -> None:
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()

    assert try_activate_existing(paths) is False

    server = SingleInstanceServer(paths)
    activated: list[bool] = []
    server.activate_requested.connect(lambda: activated.append(True))
    assert server.start() is True
    try:
        assert try_activate_existing(paths) is True
        qapp.processEvents()
        assert activated == [True]
    finally:
        server.close()

    # After server closes, no stale lock blocks a fresh start.
    assert try_activate_existing(paths) is False


def test_home_error_actions_copy_and_open_diagnostics(qapp, tmp_path) -> None:
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    (paths.logs_dir / "daemon.log").write_text(
        "\x1b[32mSTT ready\x1b[0m\nRuntimeError: Failed to open V4L2 capture on /dev/video0\n"
    )

    class ErrorController(NullController):
        def __init__(self) -> None:
            super().__init__()
            self.failure_report = inspect_daemon_error(paths.logs_dir / "daemon.log", 1)
            self._status = SessionStatus(
                SessionState.ERROR,
                f"{self.failure_report.summary}. {self.failure_report.hint}",
            )

    window = MainWindow(
        paths=paths,
        config=AppConfig(),
        controller=ErrorController(),
        theme=ThemeName.DARK,
        enable_previews=False,
    )
    try:
        window.show()
        window.home.refresh()
        qapp.processEvents()
        assert window.home.error_actions.isHidden() is False
        assert "Camera is busy" in window.home.headline.text()

        window.home.copy_error_btn.click()
        clip = QGuiApplication.clipboard().text()
        assert "Camera is busy" in clip
        assert "Failed to open V4L2 capture" in clip
        assert "\x1b[" not in clip

        window.home.view_error_btn.click()
        qapp.processEvents()
        assert window.stack.currentWidget() is window.pages["Settings"]
        assert window.settings.diagnostics.toggle_button.isChecked() is True
        assert "Failed to open V4L2 capture" in window.settings.diag_text.toPlainText()
    finally:
        window.shutdown()
        window.close()
