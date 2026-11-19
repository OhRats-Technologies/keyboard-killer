"""Main window shell: status, navigation, setup, settings, history, diagnostics."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.diagnostics.doctor import collect_doctor, format_text
from keyboard_killer.platform.linux.cameras import list_cameras
from keyboard_killer.ui.history.db import HistoryStore
from keyboard_killer.ui.history.window import HistoryPanel
from keyboard_killer.ui.onboarding.flow import OnboardingFlow
from keyboard_killer.ui.onboarding.steps import OnboardingWindow
from keyboard_killer.ui.session import NullController, SessionController, SessionState
from keyboard_killer.ui.settings.window import SettingsPanel
from keyboard_killer.ui.theme.tokens import ThemeName, stylesheet, tokens_for
from keyboard_killer.voice.stt.factory import stt_report

VERSION = "0.1.0"


class HomePanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Keyboard-Killer")
        title.setObjectName("Title")
        layout.addWidget(title)
        self.status = QLabel("Stopped")
        self.status.setObjectName("Pill")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMaximumWidth(220)
        layout.addWidget(self.status)
        self.detail = QLabel("Set up cameras and calibration, then start listening.")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        self.primary = QPushButton("Start listening")
        self.primary.setObjectName("Primary")
        self.primary.clicked.connect(self.toggle)
        actions.addWidget(self.primary)
        setup = QPushButton("Open setup")
        setup.clicked.connect(window.open_onboarding)
        actions.addWidget(setup)
        diagnostics = QPushButton("Diagnostics")
        diagnostics.clicked.connect(lambda: window.show_page("Diagnostics"))
        actions.addWidget(diagnostics)
        actions.addStretch(1)
        layout.addLayout(actions)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel("Recent action"))
        self.last_action = QLabel("No actions yet.")
        self.last_action.setObjectName("Muted")
        self.last_action.setWordWrap(True)
        card_layout.addWidget(self.last_action)
        layout.addWidget(card)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        status = self.window.controller.status
        self.status.setText(status.state.value.capitalize())
        self.detail.setText(status.detail or "Ready.")
        self.primary.setText(
            "Start listening" if status.state in (SessionState.STOPPED, SessionState.ERROR, SessionState.PAUSED) else "Pause"
        )
        recent = self.window.history.recent(1)
        if recent:
            item = recent[0]
            self.last_action.setText(
                f"{item['transcript']} -> {item['route']} ({item['latency_ms']:.0f} ms)"
                if item["latency_ms"]
                else f"{item['transcript']} -> {item['route']}"
            )
        self.window.primary_action = self.primary

    def toggle(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.window.controller.pause()
        else:
            self.window.controller.start()
        self.refresh()
        self.window.refresh_tray()


class CamerasPanel(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Cameras")
        title.setObjectName("Heading")
        layout.addWidget(title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        try:
            cameras = list_cameras()
        except Exception as exc:
            self.body.setText(f"Could not list cameras: {exc}")
            return
        lines = [f"{camera.name}\n  {camera.path}" for camera in cameras] or ["No stable cameras found."]
        lines.append("")
        lines.append(f"Camera 1: {self.config.cam1_by_id or 'not set'}")
        lines.append(f"Camera 2: {self.config.cam2_by_id or 'not set'}")
        self.body.setText("\n".join(lines))


class CalibrationPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Calibration")
        title.setObjectName("Heading")
        layout.addWidget(title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        setup = QPushButton("Open setup")
        setup.clicked.connect(window.open_onboarding)
        layout.addWidget(setup, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        path = self.window.paths.calibration_path
        self.body.setText(
            f"{'Calibration is ready.' if path.exists() else 'Calibration is missing.'}\n{path}"
        )


class ModelsPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Models")
        title.setObjectName("Heading")
        layout.addWidget(title)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        layout.addWidget(self.body)
        setup = QPushButton("Open setup")
        setup.clicked.connect(window.open_onboarding)
        layout.addWidget(setup, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        report = stt_report(self.window.config, paths=self.window.paths)
        parakeet = report.get("parakeet", {})
        self.body.setText(
            f"Selected: {report.get('selected')}\n"
            f"Parakeet: {parakeet.get('state', 'unknown')}\n"
            f"Parakeet path: {parakeet.get('path', '')}\n"
            f"Whisper fallback: {'installed' if report.get('whisper', {}).get('available') else 'missing'}"
        )


class DiagnosticsPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Diagnostics")
        title.setObjectName("Heading")
        header.addWidget(title)
        header.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        self.refresh()

    def refresh(self) -> None:
        try:
            report = collect_doctor(check_providers=False)
            self.text.setPlainText(format_text(report))
        except Exception as exc:
            self.text.setPlainText(f"Diagnostics failed: {exc}")


class AboutPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("About")
        title.setObjectName("Heading")
        layout.addWidget(title)
        text = QLabel(
            f"Keyboard-Killer {VERSION}\n\n"
            "Dual-camera stereo gaze, pop-click, and voice control.\n"
            "Speech: NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0), ONNX export by istupakov.\n"
            "Camera and speech processing stay on this device. Optional AI providers are chosen by you."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    PAGES = ("Home", "Cameras", "Calibration", "Models", "Settings", "History", "Diagnostics", "About")

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        config: AppConfig | None = None,
        controller: SessionController | None = None,
        flow: OnboardingFlow | None = None,
        theme: ThemeName | str | None = None,
        enable_previews: bool = True,
        report_provider: Callable | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths or current_paths()
        self.config = config or AppConfig.load(self.paths.settings_path)
        self.controller = controller or NullController()
        self.flow = flow or OnboardingFlow(self.paths)
        self.enable_previews = enable_previews
        self.report_provider = report_provider
        self.history = HistoryStore(self.paths)
        self.primary_action: QPushButton | None = None
        self.tray = None
        self.overlay = None
        self._flow_window: OnboardingWindow | None = None

        self.setWindowTitle("Keyboard-Killer")
        self.resize(1120, 720)
        self.setMinimumSize(960, 600)

        central = QWidget()
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(224)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 16, 12, 16)
        wordmark = QLabel("Keyboard-Killer")
        wordmark.setObjectName("Heading")
        side_layout.addWidget(wordmark)
        side_layout.addSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.addItems(list(self.PAGES))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._select)
        side_layout.addWidget(self.nav, 1)
        self.side_state = QLabel("Stopped")
        self.side_state.setObjectName("Muted")
        side_layout.addWidget(self.side_state)
        row.addWidget(sidebar)

        content = QWidget()
        self.stack = QStackedWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 20)
        content_layout.addWidget(self.stack)
        row.addWidget(content, 1)
        self.setCentralWidget(central)

        self.home = HomePanel(self)
        self.pages: dict[str, QWidget] = {
            "Home": self.home,
            "Cameras": CamerasPanel(self.config),
            "Calibration": CalibrationPanel(self),
            "Models": ModelsPanel(self),
            "Settings": SettingsPanel(self.config, self.paths),
            "History": HistoryPanel(self.history),
            "Diagnostics": DiagnosticsPanel(),
            "About": AboutPanel(),
        }
        for name in self.PAGES:
            self.stack.addWidget(self.pages[name])

        self.statusBar().showMessage("Ready")
        self.set_theme(theme or ThemeName.LIGHT)
        self.refresh_tray()

    def set_theme(self, theme: ThemeName | str) -> None:
        self.theme_tokens = tokens_for(theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.theme_tokens))
        else:
            self.setStyleSheet(stylesheet(self.theme_tokens))

    def _select(self, index: int) -> None:
        if 0 <= index < len(self.PAGES):
            page = self.pages[self.PAGES[index]]
            self.stack.setCurrentWidget(page)
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def show_page(self, name: str) -> None:
        if name in self.PAGES:
            self.nav.setCurrentRow(self.PAGES.index(name))
            self._select(self.PAGES.index(name))

    def open_onboarding(self) -> OnboardingWindow:
        window = OnboardingWindow(
            flow=self.flow,
            config=self.config,
            paths=self.paths,
            report_provider=self.report_provider,
            enable_previews=self.enable_previews,
        )
        self._flow_window = window
        window.finished.connect(self._onboarding_finished)
        window.show()
        return window

    def _onboarding_finished(self, _result: int) -> None:
        self.config = AppConfig.load(self.paths.settings_path)
        self.show_page("Home")
        self.home.refresh()
        self.flow = self._flow_window.flow if self._flow_window else self.flow

    def refresh_tray(self) -> None:
        status = self.controller.status
        self.side_state.setText(status.state.value.capitalize())
        if self.tray is not None:
            self.tray.set_state(status.state, status.detail)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.tray is not None and self.tray.available():
            event.ignore()
            self.hide()
            return
        self.controller.quit()
        super().closeEvent(event)


def run_ui(
    *,
    paths: AppPaths | None = None,
    config: AppConfig | None = None,
    theme: ThemeName | str | None = None,
    controller: SessionController | None = None,
    enable_previews: bool = True,
) -> int:
    from keyboard_killer.ui.overlay.linux import create_overlay
    from keyboard_killer.ui.theme.tokens import system_theme_name
    from keyboard_killer.ui.tray import Tray

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Keyboard-Killer")
    app.setApplicationDisplayName("Keyboard-Killer")
    resolved_theme = theme or system_theme_name(app)
    window = MainWindow(
        paths=paths,
        config=config,
        controller=controller,
        theme=resolved_theme,
        enable_previews=enable_previews,
    )
    tray = Tray(
        window=window,
        on_open=window.show,
        on_pause=window.home.toggle,
        on_quit=app.quit,
        on_calibrate=window.open_onboarding,
    )
    if tray.start():
        window.tray = tray
    window.overlay = create_overlay(tray=tray.icon, env=None)
    window.show()
    return app.exec()
