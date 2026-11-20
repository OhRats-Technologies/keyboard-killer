"""Main window shell: status, navigation, setup, settings, history, diagnostics."""

from __future__ import annotations

import importlib.metadata
import os
from datetime import datetime, timezone
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
    QListWidgetItem,
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

VERSION = "0.1.2"


class GazeField(QWidget):
    """Quiet instrument placeholder; never pretends to have live gaze data."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self.live = False
        self.setMinimumSize(240, 150)

    def set_live(self, value: bool) -> None:
        self.live = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QColor, QPainter, QPen

        tokens = self.window.theme_tokens
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(tokens.surface))
        line = QColor(tokens.border)
        line.setAlpha(45)
        painter.setPen(QPen(line, 1))
        step = 20
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
        cx, cy = self.width() // 2, self.height() // 2
        painter.setPen(QPen(QColor(tokens.muted), 1))
        painter.drawLine(cx - 12, cy, cx + 12, cy)
        painter.drawLine(cx, cy - 12, cx, cy + 12)
        painter.setPen(QPen(QColor(tokens.text), 1))
        painter.drawRect(cx - 3, cy - 3, 6, 6)
        painter.setPen(QColor(tokens.muted))
        font = painter.font()
        font.setPointSize(7)
        painter.setFont(font)
        painter.drawText(10, 16, "GAZE FIELD")
        painter.drawText(
            10,
            self.height() - 12,
            "WAITING FOR VISION" if not self.live else "LIVE",
        )
        painter.end()


class HomePanel(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(40)

        left = QVBoxLayout()
        left.setSpacing(12)
        self.state_label = QLabel("STATE · OFFLINE")
        self.state_label.setObjectName("Eyebrow")
        left.addWidget(self.state_label)
        self.headline = QLabel("Stopped.")
        self.headline.setObjectName("Display")
        left.addWidget(self.headline)
        self.detail = QLabel("Set up cameras and calibration, then start listening.")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.detail.setMaximumWidth(620)
        left.addWidget(self.detail)

        actions = QHBoxLayout()
        self.primary = QPushButton("START LISTENING")
        self.primary.setObjectName("Primary")
        self.primary.clicked.connect(self.toggle)
        actions.addWidget(self.primary)
        history = QPushButton("VIEW HISTORY")
        history.clicked.connect(lambda: window.show_page("History"))
        actions.addWidget(history)
        actions.addStretch(1)
        left.addLayout(actions)

        rule = QFrame()
        rule.setObjectName("Divider")
        left.addWidget(rule)

        self.last_heard = QLabel("Nothing heard yet.")
        self.last_heard.setObjectName("Muted")
        self.last_heard.setWordWrap(True)
        self.last_clicked = QLabel("Nothing clicked yet.")
        self.last_clicked.setObjectName("Muted")
        self.last_clicked.setWordWrap(True)
        pair = QHBoxLayout()
        for title, value in (("LAST HEARD", self.last_heard), ("LAST CLICKED", self.last_clicked)):
            column = QVBoxLayout()
            column.addWidget(eyebrow(title))
            column.addWidget(value)
            column.addStretch(1)
            pair.addLayout(column, 1)
        left.addLayout(pair)

        try_row = QHBoxLayout()
        try_row.addWidget(eyebrow("TRY SAYING"))
        for text in ("click send", "scroll down", "pause control"):
            chip = QPushButton(text)
            chip.setObjectName("Chip")
            chip.setEnabled(False)
            try_row.addWidget(chip)
        try_row.addStretch(1)
        left.addLayout(try_row)
        left.addStretch(1)
        privacy = QLabel("CAMERA + VOICE LOCAL  ·  NOTHING LEAVES THIS MACHINE")
        privacy.setObjectName("Eyebrow")
        left.addWidget(privacy)
        layout.addLayout(left, 3)

        instrument = Card()
        instrument.setMaximumWidth(360)
        instrument.layout.addWidget(eyebrow("INSTRUMENT"))
        self.gaze_field = GazeField(window)
        instrument.layout.addWidget(self.gaze_field)
        self.rows = {}
        for key, label in (
            ("cameras", "Cameras"),
            ("gaze", "Gaze"),
            ("speech", "Speech model"),
            ("latency", "Latency"),
            ("mic", "Microphone"),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch(1)
            value = QLabel("—")
            value.setObjectName("Mono")
            row.addWidget(value)
            instrument.layout.addLayout(row)
            self.rows[key] = value
        details = QPushButton("VIEW DETAILS")
        details.clicked.connect(lambda: window.show_page("Diagnostics"))
        instrument.layout.addWidget(details)
        layout.addWidget(instrument, 2)
        self.refresh()

    def _set_state(self, state: str) -> None:
        self.state_label.setText(f"STATE · {state.upper()}")
        self.gaze_field.set_live(state == "listening")

    def refresh(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.headline.setText("Listening.")
            self.detail.setText(
                "Gaze and voice are on. Pause stops both and releases the camera."
            )
            self._set_state("live")
            self.primary.setText("PAUSE CONTROL")
        elif status.state is SessionState.PAUSED:
            self.headline.setText("Paused.")
            self.detail.setText(
                "Nothing is watching or listening. Resume when you are ready."
            )
            self._set_state("paused")
            self.primary.setText("RESUME")
        elif status.state is SessionState.ERROR:
            self.headline.setText("Needs attention.")
            self.detail.setText(status.detail or "Open diagnostics for the next action.")
            self._set_state("needs attention")
            self.primary.setText("START LISTENING")
        else:
            self.headline.setText("Stopped.")
            self.detail.setText(
                status.detail or "Set up cameras and calibration, then start listening."
            )
            self._set_state("offline")
            self.primary.setText("START LISTENING")

        recent = self.window.history.recent(2)
        heard = next((item for item in recent if item.get("transcript")), None)
        clicked = next((item for item in recent if item.get("target") or item.get("route")), None)
        self.last_heard.setText(
            f"“{heard['transcript']}”" if heard else "Nothing heard yet."
        )
        self.last_clicked.setText(
            f"{clicked['target'] or clicked['route']}" if clicked else "Nothing clicked yet."
        )

        from keyboard_killer.platform.linux.cameras import resolve_camera_config
        from keyboard_killer.voice.stt.factory import stt_report

        mode, cam1, cam2 = resolve_camera_config(
            self.window.config.camera_mode,
            self.window.config.cam1_by_id,
            self.window.config.cam2_by_id,
        )
        self.rows["cameras"].setText(f"{mode.upper()} · {'2' if cam2 else '1'}")
        calibrated = self.window.paths.calibration_path.exists()
        self.rows["gaze"].setText("CALIBRATED" if calibrated else "NOT CALIBRATED")
        speech = stt_report(self.window.config, paths=self.window.paths)
        self.rows["speech"].setText(
            "READY" if speech.get("selected") == "parakeet" else "DOWNLOAD NEEDED"
        )
        latency = next((item["latency_ms"] for item in recent if item.get("latency_ms")), None)
        self.rows["latency"].setText(f"{latency:.0f} MS" if latency else "—")
        self.rows["mic"].setText("READY" if status.state is SessionState.LISTENING else "OFF")
        self.window.primary_action = self.primary

    def toggle(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.window.controller.pause()
        else:
            self.window.controller.start()
        self.refresh()
        self.window.refresh_tray()


class SetupPanel(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Setup")
        title.setObjectName("Heading")
        layout.addWidget(title)
        card = Card()
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setObjectName("Muted")
        card.layout.addWidget(self.body)
        action = QPushButton("OPEN SETUP")
        action.setObjectName("Primary")
        action.clicked.connect(window.open_onboarding)
        card.layout.addWidget(action, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        if self.window.flow.is_complete:
            self.body.setText(
                "Setup is complete. Review permissions, cameras, calibration, and the speech model."
            )
        else:
            self.body.setText(
                "About 10 minutes. Permissions, one or two cameras, calibration, the speech model, "
                "an optional AI provider, and one practice click. Your progress is saved."
            )


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
            "One- or two-camera gaze, pop-click, and voice control.\n"
            "Speech: NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0), ONNX export by istupakov.\n"
            "Camera and speech processing stay on this device. Optional AI providers are chosen by you.\n\n"
            "Type: Inter and Space Mono (SIL Open Font License)."
        )
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    PAGES = (
        "Home",
        "Setup",
        "Cameras",
        "Calibration",
        "Models",
        "History",
        "Diagnostics",
        "Settings",
        "About",
    )

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
        tagline = QLabel("GAZE + VOICE")
        tagline.setObjectName("Eyebrow")
        side_layout.addWidget(tagline)
        side_layout.addSpacing(16)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav_rows: dict[str, int] = {}
        self._build_nav()
        self.nav.setCurrentRow(self.nav_rows["Home"])
        self.nav.currentRowChanged.connect(self._select)
        side_layout.addWidget(self.nav, 1)
        footer = QLabel("LOCAL ONLY  ·  NO ACCOUNT")
        footer.setObjectName("Eyebrow")
        side_layout.addWidget(footer)
        row.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("Content")
        self.stack = QStackedWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        topbar = QFrame()
        topbar.setObjectName("TopBar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(28, 10, 28, 10)
        self.breadcrumb = QLabel("KEYBOARD-KILLER — HOME")
        self.breadcrumb.setObjectName("Breadcrumb")
        topbar_layout.addWidget(self.breadcrumb)
        topbar_layout.addStretch(1)
        self.top_last = QLabel("")
        self.top_last.setObjectName("Breadcrumb")
        topbar_layout.addWidget(self.top_last)
        self.theme_button = QPushButton("DARK")
        self.theme_button.setObjectName("Chip")
        self.theme_button.clicked.connect(self.toggle_theme)
        topbar_layout.addWidget(self.theme_button)
        self.status_chip = QLabel("STOPPED")
        self.status_chip.setObjectName("Pill")
        topbar_layout.addWidget(self.status_chip)
        content_layout.addWidget(topbar)
        page_host = QWidget()
        page_layout = QVBoxLayout(page_host)
        page_layout.setContentsMargins(28, 24, 28, 24)
        page_layout.addWidget(self.stack)
        content_layout.addWidget(page_host, 1)
        row.addWidget(content, 1)
        self.setCentralWidget(central)

        self.home = HomePanel(self)
        self.pages: dict[str, QWidget] = {
            "Home": self.home,
            "Setup": SetupPanel(self),
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

    def _build_nav(self) -> None:
        groups = (
            ("CONTROL", ("Home", "Setup", "Cameras", "Calibration", "Models")),
            ("RECORD", ("History", "Diagnostics")),
            ("SYSTEM", ("Settings", "About")),
        )
        number = 0
        for heading, entries in groups:
            header = QListWidgetItem(heading)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.nav.addItem(header)
            for page in entries:
                number += 1
                item = QListWidgetItem(f"{number:02d}  {page}")
                item.setData(Qt.ItemDataRole.UserRole, page)
                self.nav.addItem(item)
                self.nav_rows[page] = self.nav.count() - 1

    def set_theme(self, theme: ThemeName | str) -> None:
        self.theme_tokens = tokens_for(theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.theme_tokens))
        else:
            self.setStyleSheet(stylesheet(self.theme_tokens))
        if hasattr(self, "theme_button"):
            self.theme_button.setText(
                "LIGHT" if self.theme_tokens.name is ThemeName.DARK else "DARK"
            )

    def toggle_theme(self) -> None:
        self.set_theme(
            ThemeName.LIGHT
            if self.theme_tokens.name is ThemeName.DARK
            else ThemeName.DARK
        )

    def _select(self, index: int) -> None:
        item = self.nav.item(index)
        name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if name in self.pages:
            page = self.pages[name]
            self.stack.setCurrentWidget(page)
            self.breadcrumb.setText(f"KEYBOARD-KILLER — {str(name).upper()}")
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def show_page(self, name: str) -> None:
        if name in self.pages:
            self.nav.setCurrentRow(self.nav_rows.get(name, 0))
            self._select(self.nav_rows.get(name, 0))

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
        label = {
            SessionState.STOPPED: "STOPPED",
            SessionState.LISTENING: "LISTENING",
            SessionState.PAUSED: "PAUSED",
            SessionState.ERROR: "NEEDS ATTENTION",
        }.get(status.state, status.state.value.upper())
        self.status_chip.setText(label)
        recent = self.history.recent(1)
        if recent:
            try:
                created = datetime.fromisoformat(str(recent[0]["created_at"]))
                age = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
                self.top_last.setText(f"LAST ACTION {age}s AGO")
            except (TypeError, ValueError):
                self.top_last.setText("LAST ACTION RECORDED")
        else:
            self.top_last.setText("")
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
