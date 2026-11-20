"""Main window shell: status, navigation, setup, settings, history, diagnostics."""

from __future__ import annotations

import importlib.metadata
import os
import time
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.audio.quick_test import DEFAULT_SECONDS, SpeechTestResult, run_speech_test
from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.diagnostics.doctor import collect_doctor, format_text
from keyboard_killer.platform.linux.cameras import list_cameras, resolve_camera_config
from keyboard_killer.platform.linux.display import get_display_layout
from keyboard_killer.platform.linux.pipewire import pipewire_available, pipewire_note
from keyboard_killer.ui.components import Card, eyebrow, mono, status as status_label
from keyboard_killer.ui.gaze_field import GazeField
from keyboard_killer.ui.gaze_region import calibration_region
from keyboard_killer.ui.history.db import HistoryStore
from keyboard_killer.ui.history.window import HistoryPanel
from keyboard_killer.ui.onboarding.flow import OnboardingFlow
from keyboard_killer.ui.onboarding.steps import OnboardingWindow
from keyboard_killer.ui.screen_cursor import ScreenCursor
from keyboard_killer.ui.session import DaemonController, NullController, SessionController, SessionState
from keyboard_killer.ui.settings.window import SettingsPanel
from keyboard_killer.ui.theme.tokens import ThemeName, load_bundled_fonts, stylesheet, tokens_for
from keyboard_killer.voice.stt.factory import stt_report

VERSION = "0.1.2"


class HomePanel(QWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self._last_state: SessionState | None = None
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
        instrument.setMaximumWidth(390)
        instrument.layout.addWidget(eyebrow("GAZE INSTRUMENT"))
        layout_info = window.display_layout
        self.live_region = calibration_region(window.paths.calibration_path, layout_info)
        self.gaze_field = GazeField(
            window,
            source_size=(layout_info.total_width, layout_info.total_height),
            live_region=self.live_region,
        )
        self.gaze_field.setMinimumHeight(190)
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

        self.cursor_button = QPushButton("SHOW SCREEN CURSOR")
        self.cursor_button.setCheckable(True)
        self.cursor_button.clicked.connect(self._toggle_cursor)
        instrument.layout.addWidget(self.cursor_button)
        self.cursor_note = QLabel("A click-through cursor moves across the desktop.")
        self.cursor_note.setObjectName("Mono")
        self.cursor_note.setWordWrap(True)
        instrument.layout.addWidget(self.cursor_note)

        details = QPushButton("VIEW DETAILS")
        details.clicked.connect(lambda: window.show_page("Diagnostics"))
        instrument.layout.addWidget(details)
        layout.addWidget(instrument, 2)
        self.gaze_field.mode_changed.connect(self._on_gaze_mode)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(1000)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._cursor_timer.start()
        self.refresh()

    def _set_state(self, state: str) -> None:
        self.state_label.setText(f"STATE · {state.upper()}")

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
        if calibrated:
            connector = self.live_region.connector
            if connector in ("", "DEFAULT"):
                connector = self.window.display_layout.primary.connector
            self.rows["gaze"].setText(f"CALIBRATED · {connector}")
        else:
            self.rows["gaze"].setText("NOT CALIBRATED")
        speech = stt_report(self.window.config, paths=self.window.paths)
        self.rows["speech"].setText(
            "READY" if speech.get("selected") == "parakeet" else "DOWNLOAD NEEDED"
        )
        latency = next((item["latency_ms"] for item in recent if item.get("latency_ms")), None)
        self.rows["latency"].setText(f"{latency:.0f} MS" if latency else "—")
        self.rows["mic"].setText("READY" if status.state is SessionState.LISTENING else "OFF")
        self.window.primary_action = self.primary
        self._sync_cursor_controls()

    def _toggle_cursor(self, checked: bool) -> None:
        if checked:
            status = self.window.screen_cursor.start(self.live_region)
            if not status.running:
                self.cursor_button.blockSignals(True)
                self.cursor_button.setChecked(False)
                self.cursor_button.blockSignals(False)
                self.cursor_note.setText(status.error or "Screen cursor unavailable.")
                return
        else:
            self.window.screen_cursor.stop()
        self._sync_cursor_controls()

    def _poll_cursor(self) -> None:
        status = self.window.screen_cursor.refresh()
        if not status.running and self.cursor_button.isChecked():
            self.cursor_button.blockSignals(True)
            self.cursor_button.setChecked(False)
            self.cursor_button.blockSignals(False)
        if status.error:
            self.cursor_note.setText(status.error)
        elif status.running:
            self._sync_cursor_controls()

    def _sync_cursor_controls(self) -> None:
        running = self.window.screen_cursor.running
        self.cursor_button.blockSignals(True)
        self.cursor_button.setChecked(running)
        self.cursor_button.blockSignals(False)
        self.cursor_button.setText("HIDE SCREEN CURSOR" if running else "SHOW SCREEN CURSOR")
        if not running:
            if not self.window.screen_cursor.error:
                self.cursor_note.setText("A click-through cursor moves across the desktop.")
            return
        mode = self.gaze_field.mode
        if mode == "live":
            self.cursor_note.setText("ON · FOLLOWING LIVE GAZE")
        elif mode == "no-face":
            self.cursor_note.setText("ON · LIVE, NO FACE IN VIEW")
        else:
            self.cursor_note.setText("ON · DEMO PATH (NO VISION DATA)")

    def _on_gaze_mode(self, _mode: str) -> None:
        if self.window.screen_cursor.running:
            self._sync_cursor_controls()

    def poll_state(self) -> None:
        state = self.window.controller.status.state
        if state is not self._last_state:
            self._last_state = state
            self.refresh()
            self.window.refresh_tray()

    def shutdown(self) -> None:
        self._cursor_timer.stop()
        self.gaze_field.shutdown()
        self.window.screen_cursor.stop()

    def toggle(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.window.controller.pause()
        else:
            self.window.controller.start()
        self._last_state = None
        self.poll_state()


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

        sharing = Card()
        sharing.layout.addWidget(eyebrow("CAMERA SHARING"))
        self.share_body = QLabel("")
        self.share_body.setObjectName("Muted")
        self.share_body.setWordWrap(True)
        sharing.layout.addWidget(self.share_body)
        self.share_button = QPushButton("SHARE CAMERA WITH OTHER APPS")
        self.share_button.setCheckable(True)
        self.share_button.clicked.connect(self._toggle_sharing)
        sharing.layout.addWidget(self.share_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(sharing)
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

        available = pipewire_available()
        shared = self.window.config.camera_backend == "pipewire"
        usable = available and mode == "mono"
        self.share_button.blockSignals(True)
        self.share_button.setChecked(shared)
        self.share_button.blockSignals(False)
        self.share_button.setEnabled(usable)
        self.share_button.setText(
            "PIPEWIRE SHARING ON" if shared else "SHARE CAMERA WITH OTHER APPS"
        )
        if not available:
            self.share_body.setText(pipewire_note())
        elif mode != "mono":
            self.share_body.setText(
                "Sharing uses the PipeWire default source and is available in mono mode. "
                "Switch camera mode to mono, or keep V4L2 for stereo."
            )
        elif shared:
            self.share_body.setText(
                "PipeWire sharing is on. The app no longer holds /dev/videoN, so other "
                "camera clients can request it through the portal. The portal provides "
                "640x480 and exposure locks are unavailable; check that your eyes are "
                "inside the frame and recalibrate in this mode for the best accuracy."
            )
        else:
            self.share_body.setText(
                "The V4L2 backend owns the camera exclusively. Turn on sharing to capture "
                "through PipeWire so other camera clients can request it through the portal. "
                "Mono only; 640x480; no manual exposure."
            )

    def _toggle_sharing(self, checked: bool) -> None:
        self.window.config.camera_backend = "pipewire" if checked else "v4l2"
        self.window.config.save(self.window.paths.settings_path)
        if self.window.controller.status.state is SessionState.LISTENING:
            self.window.controller.pause()
            self.window.controller.start()
            self.window.home.poll_state()
        self.refresh()


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


class SpeechTestWorker(QThread):
    """Load Parakeet and transcribe one microphone clip off the UI thread."""

    stage = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window

    def run(self) -> None:
        try:
            result = run_speech_test(
                self.window.config,
                paths=self.window.paths,
                seconds=DEFAULT_SECONDS,
                on_stage=self.stage.emit,
            )
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelsPanel(QWidget):
    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        self._worker: SpeechTestWorker | None = None
        self._record_started = 0.0
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

        test = Card()
        test.layout.addWidget(eyebrow("LIVE SPEECH TEST"))
        test_title = QLabel("Say something. Read the transcript.")
        test_title.setObjectName("Heading")
        test.layout.addWidget(test_title)
        test_copy = QLabel(
            "Records four seconds from the default microphone and runs the app-owned "
            "Parakeet model on this device. No audio leaves the machine."
        )
        test_copy.setObjectName("Muted")
        test_copy.setWordWrap(True)
        test.layout.addWidget(test_copy)
        self.test_status = QLabel("READY · LOCAL PARAKEET")
        self.test_status.setObjectName("Mono")
        test.layout.addWidget(self.test_status)
        self.test_progress = QProgressBar()
        self.test_progress.setRange(0, 100)
        self.test_progress.setVisible(False)
        test.layout.addWidget(self.test_progress)
        self.test_transcript = QLabel("—")
        self.test_transcript.setObjectName("Heading")
        self.test_transcript.setWordWrap(True)
        test.layout.addWidget(self.test_transcript)
        self.test_detail = QLabel("")
        self.test_detail.setObjectName("Mono")
        self.test_detail.setWordWrap(True)
        test.layout.addWidget(self.test_detail)
        self.test_button = QPushButton("RECORD 4 SECONDS")
        self.test_button.setObjectName("Primary")
        self.test_button.clicked.connect(self.start_test)
        test.layout.addWidget(self.test_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(test)
        layout.addStretch(1)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._tick_progress)
        self.refresh()

    def refresh(self) -> None:
        report = stt_report(self.window.config, paths=self.window.paths)
        parakeet = report.get("parakeet", {})
        ready = report.get("selected") == "parakeet"
        self.status.setText("READY" if ready else "DOWNLOAD NEEDED")
        self.status.setObjectName("Success" if ready else "Warning")
        self.path.setText(f"FILE  {parakeet.get('path', '')}")
        idle = self._worker is None or not self._worker.isRunning()
        if not ready and idle:
            self.test_status.setText("SPEECH MODEL MISSING · OPEN SETUP TO DOWNLOAD")
            self.test_button.setEnabled(False)
        elif idle:
            self.test_button.setEnabled(True)

    def start_test(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        report = stt_report(self.window.config, paths=self.window.paths)
        if report.get("selected") != "parakeet":
            self.test_status.setText("SPEECH MODEL MISSING · OPEN SETUP TO DOWNLOAD")
            return
        self.test_transcript.setText("—")
        self.test_detail.setText("")
        self.test_progress.setVisible(True)
        self.test_progress.setRange(0, 0)
        self.test_button.setEnabled(False)
        self.test_button.setText("TEST RUNNING")
        self.test_status.setObjectName("Mono")
        self.test_status.style().unpolish(self.test_status)
        self.test_status.style().polish(self.test_status)
        worker = SpeechTestWorker(self.window)
        worker.stage.connect(self._on_stage)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_stage(self, stage: str) -> None:
        if stage == "loading":
            self.test_status.setText("LOADING PARAKEET…")
            self.test_progress.setRange(0, 0)
        elif stage == "recording":
            self._record_started = time.monotonic()
            self.test_status.setText("RECORDING · SPEAK NOW")
            self.test_progress.setRange(0, 100)
            self.test_progress.setValue(0)
            self._progress_timer.start()
        elif stage == "transcribing":
            self._progress_timer.stop()
            self.test_progress.setRange(0, 0)
            self.test_status.setText("TRANSCRIBING…")

    def _tick_progress(self) -> None:
        import time

        elapsed = time.monotonic() - self._record_started
        remaining = max(0.0, DEFAULT_SECONDS - elapsed)
        self.test_progress.setValue(
            min(100, int(100 * elapsed / max(0.1, DEFAULT_SECONDS)))
        )
        self.test_status.setText(f"RECORDING · {remaining:.1f}S LEFT")

    def _on_done(self, result: SpeechTestResult) -> None:
        self._progress_timer.stop()
        self.test_progress.setVisible(False)
        self.test_button.setEnabled(True)
        self.test_button.setText("TEST AGAIN")
        self.test_status.setText(
            "HEARD · LOCAL PARAKEET" if result.has_speech else "NO SPEECH DETECTED"
        )
        self.test_status.setObjectName("Success" if result.has_speech else "Warning")
        self.test_status.style().unpolish(self.test_status)
        self.test_status.style().polish(self.test_status)
        self.test_transcript.setText(
            f"“{result.transcript}”" if result.transcript else "No words recognized."
        )
        self.test_detail.setText(
            f"{result.latency_ms:.0f} MS · {result.recorded_s:.1f}S · "
            f"PEAK {result.peak:.3f} · {result.engine} · {result.sample_rate} HZ"
        )

    def _on_failed(self, message: str) -> None:
        self._progress_timer.stop()
        self.test_progress.setVisible(False)
        self.test_button.setEnabled(True)
        self.test_button.setText("TRY AGAIN")
        self.test_status.setText("TEST FAILED")
        self.test_status.setObjectName("Warning")
        self.test_status.style().unpolish(self.test_status)
        self.test_status.style().polish(self.test_status)
        self.test_transcript.setText("—")
        self.test_detail.setText(message)

    def stop_test(self) -> None:
        self._progress_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(8000)


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
        self.screen_cursor = ScreenCursor(paths=self.paths)
        self.flow = flow or OnboardingFlow(self.paths)
        self.enable_previews = enable_previews
        self.report_provider = report_provider
        self.history = HistoryStore(self.paths)
        self.primary_action: QPushButton | None = None
        self.tray = None
        self.overlay = None
        self._flow_window: OnboardingWindow | None = None
        self._display_layout = None
        self._shutdown = False

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
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self.home.poll_state)
        self._status_timer.start()

    @property
    def display_layout(self):
        if self._display_layout is None:
            self._display_layout = get_display_layout()
        return self._display_layout

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        if hasattr(self, "_status_timer"):
            self._status_timer.stop()
        models = getattr(self, "pages", {}).get("Models")
        if models is not None:
            models.stop_test()
        self.home.shutdown()
        self.controller.quit()

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
        self.shutdown()
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
        controller=controller or DaemonController(paths=paths),
        theme=resolved_theme,
        enable_previews=enable_previews,
    )
    app.aboutToQuit.connect(window.shutdown)
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
