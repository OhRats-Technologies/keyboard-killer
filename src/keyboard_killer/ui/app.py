"""Main window shell: status, navigation, setup, settings, history, diagnostics."""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
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
from keyboard_killer.platform.linux.cameras import list_cameras, resolve_camera_config
from keyboard_killer.ui.components import Card, eyebrow, mono, status as status_label
from keyboard_killer.ui.history.db import HistoryStore
from keyboard_killer.ui.history.window import HistoryPanel
from keyboard_killer.ui.onboarding.flow import OnboardingFlow
from keyboard_killer.ui.onboarding.steps import OnboardingWindow
from keyboard_killer.ui.session import NullController, SessionController, SessionState
from keyboard_killer.ui.settings.window import SettingsPanel
from keyboard_killer.ui.theme.tokens import ThemeName, load_bundled_fonts, stylesheet, tokens_for
from keyboard_killer.voice.stt.factory import stt_report

VERSION = "0.1.1"


class HomePanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Keyboard-Killer")
        title.setObjectName("Title")
        layout.addWidget(title)
        eyebrow_label = QLabel("LOCAL GAZE + VOICE")
        eyebrow_label.setObjectName("Eyebrow")
        layout.addWidget(eyebrow_label)
        self.status = QLabel("STOPPED")
        self.status.setObjectName("Pill")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setMaximumWidth(220)
        layout.addWidget(self.status)
        self.detail = QLabel("Set up cameras and calibration, then start listening.")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        self.primary = QPushButton("START LISTENING")
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

        setup = QFrame()
        setup.setObjectName("Card")
        setup_layout = QVBoxLayout(setup)
        head = QHBoxLayout()
        head.addWidget(eyebrow("SETUP"))
        head.addStretch(1)
        finish = QPushButton("OPEN SETUP")
        finish.clicked.connect(window.open_onboarding)
        head.addWidget(finish)
        setup_layout.addLayout(head)
        self.checklist = {}
        for key, label in (
            ("input", "Input and microphone"),
            ("cameras", "Cameras"),
            ("calibration", "Calibration"),
            ("speech", "Speech model"),
            ("provider", "Optional AI provider"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            value = QLabel("")
            value.setObjectName("Muted")
            row.addWidget(value)
            setup_layout.addLayout(row)
            self.checklist[key] = value
        layout.addWidget(setup)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(eyebrow("LAST ACTION"))
        self.last_action = QLabel("No actions yet.")
        self.last_action.setObjectName("Muted")
        self.last_action.setWordWrap(True)
        card_layout.addWidget(self.last_action)
        layout.addWidget(card)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        status = self.window.controller.status
        self.status.setText(status.state.value.upper())
        self.detail.setText(status.detail or "Ready.")
        self.primary.setText(
            "START LISTENING"
            if status.state in (SessionState.STOPPED, SessionState.ERROR, SessionState.PAUSED)
            else "PAUSE"
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
        self._refresh_checklist()

    def _refresh_checklist(self) -> None:
        config = self.window.config
        uinput = Path("/dev/uinput").exists() and os.access("/dev/uinput", os.W_OK)
        inputs = bool(Path("/dev/input").glob("event*"))
        self.checklist["input"].setText(
            "READY" if uinput and inputs else "NEEDS SETUP"
        )
        self.checklist["input"].setObjectName("Success" if uinput and inputs else "Warning")
        from keyboard_killer.platform.linux.cameras import resolve_camera_config
        from keyboard_killer.voice.stt.factory import stt_report

        mode, cam1, cam2 = resolve_camera_config(
            config.camera_mode, config.cam1_by_id, config.cam2_by_id
        )
        self.checklist["cameras"].setText(
            f"{mode.upper()} · {'2 CAMERAS' if cam2 else '1 CAMERA'}"
        )
        self.checklist["cameras"].setObjectName("Success" if cam1 else "Warning")
        calibrated = self.window.paths.calibration_path.exists()
        self.checklist["calibration"].setText("READY" if calibrated else "NEEDS SETUP")
        self.checklist["calibration"].setObjectName("Success" if calibrated else "Warning")
        speech = stt_report(config, paths=self.window.paths)
        ready = speech.get("selected") == "parakeet"
        self.checklist["speech"].setText("READY" if ready else "DOWNLOAD NEEDED")
        self.checklist["speech"].setObjectName("Success" if ready else "Warning")
        self.checklist["provider"].setText("OPTIONAL")
        self.checklist["provider"].setObjectName("Muted")

    def toggle(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.window.controller.pause()
        else:
            self.window.controller.start()
        self.refresh()
        self.window.refresh_tray()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        tokens = self.window.theme_tokens
        color = QColor(tokens.border)
        color.setAlpha(28)
        painter = QPainter(self)
        painter.setPen(QPen(color, 1))
        step = 40
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
        painter.end()


class CamerasPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Cameras")
        title.setObjectName("Heading")
        head.addWidget(title)
        head.addStretch(1)
        setup = QPushButton("OPEN SETUP")
        setup.clicked.connect(window.open_onboarding)
        head.addWidget(setup)
        layout.addLayout(head)
        self.card = Card()
        layout.addWidget(self.card)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.card.layout.addWidget(self.body)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        try:
            cameras = list_cameras()
        except Exception as exc:
            self.body.setText(f"Could not list cameras: {exc}")
            return
        mode, cam1, cam2 = resolve_camera_config(
            self.window.config.camera_mode,
            self.window.config.cam1_by_id,
            self.window.config.cam2_by_id,
        )
        lines = [f"MODE  {mode.upper()}", ""]
        lines += [f"{camera.name}\n  {camera.path}" for camera in cameras] or ["No stable cameras found."]
        lines += ["", f"Camera 1  {cam1 or 'not set'}", f"Camera 2  {cam2 or 'not used in mono mode'}"]
        self.body.setText("\n".join(lines))


class CalibrationPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Calibration")
        title.setObjectName("Heading")
        head.addWidget(title)
        head.addStretch(1)
        setup = QPushButton("OPEN SETUP")
        setup.clicked.connect(window.open_onboarding)
        head.addWidget(setup)
        layout.addLayout(head)
        card = Card()
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        card.layout.addWidget(eyebrow("STATUS"))
        card.layout.addWidget(self.status)
        self.detail = QLabel(
            "Calibration teaches the gaze model your screen and posture. "
            "It opens a full-screen target window."
        )
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        card.layout.addWidget(self.detail)
        self.advanced = QLabel("")
        self.advanced.setObjectName("Mono")
        self.advanced.setWordWrap(True)
        card.layout.addWidget(self.advanced)
        layout.addWidget(card)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        path = self.window.paths.calibration_path
        ready = path.exists()
        self.status.setText("READY" if ready else "NOT CALIBRATED")
        self.status.setObjectName("Success" if ready else "Warning")
        self.detail.setText(
            "Calibration is ready. Re-run it after moving the cameras or monitor."
            if ready
            else "Run the guided full-screen calibration before starting vision."
        )
        self.advanced.setText(f"FILE  {path}")
        self.advanced.setVisible(False)


class ModelsPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Models")
        title.setObjectName("Heading")
        head.addWidget(title)
        head.addStretch(1)
        setup = QPushButton("OPEN SETUP")
        setup.clicked.connect(window.open_onboarding)
        head.addWidget(setup)
        layout.addLayout(head)
        card = Card()
        card.layout.addWidget(eyebrow("SPEECH"))
        name = QLabel("Parakeet TDT 0.6B v2")
        name.setObjectName("Heading")
        card.layout.addWidget(name)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        card.layout.addWidget(self.status)
        desc = QLabel("English · ONNX int8 · CPU by default · about 630 MB")
        desc.setObjectName("Muted")
        card.layout.addWidget(desc)
        self.path = QLabel("")
        self.path.setObjectName("Mono")
        self.path.setWordWrap(True)
        card.layout.addWidget(self.path)
        layout.addWidget(card)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        report = stt_report(self.window.config, paths=self.window.paths)
        parakeet = report.get("parakeet", {})
        ready = report.get("selected") == "parakeet"
        self.status.setText("READY" if ready else "DOWNLOAD NEEDED")
        self.status.setObjectName("Success" if ready else "Warning")
        self.path.setText(f"FILE  {parakeet.get('path', '')}")


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
        refresh = QPushButton("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        subtitle = QLabel("The same report used by setup and the tray.")
        subtitle.setObjectName("Muted")
        layout.addWidget(subtitle)

        summary = Card()
        grid = QGridLayout()
        self.badges: dict[str, QLabel] = {}
        for row, (key, label) in enumerate(
            (
                ("input", "Input"),
                ("cameras", "Cameras"),
                ("screen", "Screen"),
                ("speech", "Speech"),
                ("ai", "AI provider"),
                ("overlay", "Overlay"),
            )
        ):
            name = QLabel(label)
            value = QLabel("CHECKING")
            value.setObjectName("Muted")
            grid.addWidget(name, row, 0)
            grid.addWidget(value, row, 1)
            self.badges[key] = value
        grid.setColumnStretch(1, 1)
        summary.layout.addLayout(grid)
        layout.addWidget(summary)

        detail_label = eyebrow("TECHNICAL DETAIL")
        layout.addWidget(detail_label)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)
        self.refresh()

    @staticmethod
    def _set_badge(label: QLabel, text: str, ok: bool | None) -> None:
        label.setText(text.upper())
        label.setObjectName("Success" if ok is True else "Warning" if ok is False else "Muted")
        label.style().unpolish(label)
        label.style().polish(label)

    def refresh(self) -> None:
        try:
            report = collect_doctor(check_providers=False)
            caps = report.capabilities.get("capabilities", {})
            self._set_badge(
                self.badges["input"],
                caps.get("pointer", {}).get("status", "unknown"),
                caps.get("pointer", {}).get("status") == "available",
            )
            cameras = report.cameras.get("mode", "unknown")
            self._set_badge(self.badges["cameras"], cameras, cameras in ("mono", "stereo"))
            self._set_badge(
                self.badges["screen"],
                caps.get("screen_capture", {}).get("status", "unknown"),
                caps.get("screen_capture", {}).get("status") == "available",
            )
            self._set_badge(
                self.badges["speech"],
                report.stt.get("selected", "missing"),
                report.stt.get("selected") == "parakeet",
            )
            text_status = report.providers.get("text_llm", {}).get("status", {}).get("state", "not checked")
            self._set_badge(self.badges["ai"], text_status, text_status == "ready")
            self._set_badge(
                self.badges["overlay"],
                caps.get("overlay", {}).get("status", "unknown"),
                caps.get("overlay", {}).get("status") == "available",
            )
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
            "Camera and speech processing stay on this device. Optional AI providers are chosen by you.\n\n"
            "Type: Inter and Space Mono (SIL Open Font License)."
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
        wordmark = QLabel("KEYBOARD-KILLER")
        wordmark.setObjectName("Wordmark")
        side_layout.addWidget(wordmark)
        side_layout.addSpacing(12)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.addItems(list(self.PAGES))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._select)
        side_layout.addWidget(self.nav, 1)
        self.side_state = QLabel("STOPPED")
        self.side_state.setObjectName("Mono")
        side_layout.addWidget(self.side_state)
        row.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("Content")
        self.stack = QStackedWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 20, 24, 20)
        content_layout.addWidget(self.stack)
        row.addWidget(content, 1)
        self.setCentralWidget(central)

        self.home = HomePanel(self)
        self.pages: dict[str, QWidget] = {
            "Home": self.home,
            "Cameras": CamerasPanel(self),
            "Calibration": CalibrationPanel(self),
            "Models": ModelsPanel(self),
            "Settings": SettingsPanel(self.config, self.paths),
            "History": HistoryPanel(self.history),
            "Diagnostics": DiagnosticsPanel(),
            "About": AboutPanel(),
        }
        for name in self.PAGES:
            self.stack.addWidget(self.pages[name])

        self.statusBar().showMessage("READY")
        load_bundled_fonts()
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
        self.side_state.setText(status.state.value.upper())
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
    load_bundled_fonts()
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
    # First launch is a guided story, not a cold dashboard.
    if not window.flow.is_complete:
        window.open_onboarding()
    return app.exec()
