"""Main window shell: 3 calm views (Home, Activity, Settings) + inline first-run setup."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.platform.linux.cameras import resolve_camera_config
from keyboard_killer.platform.linux.display import get_display_layout
from keyboard_killer.ui.components import Card, divider, eyebrow
from keyboard_killer.ui.gaze_field import GazeField
from keyboard_killer.ui.gaze_region import calibration_region
from keyboard_killer.ui.history.db import HistoryStore
from keyboard_killer.ui.history.window import HistoryPanel
from keyboard_killer.ui.onboarding.flow import OnboardingFlow
from keyboard_killer.ui.onboarding.steps import OnboardingWidget, OnboardingWindow
from keyboard_killer.ui.screen_cursor import ScreenCursor
from keyboard_killer.ui.session import DaemonController, NullController, SessionController, SessionState
from keyboard_killer.ui.settings.window import SettingsPanel
from keyboard_killer.ui.theme.tokens import ThemeName, load_bundled_fonts, stylesheet, tokens_for
from keyboard_killer.ui.transcript_overlay import TranscriptOverlayController
from keyboard_killer.ui.tray import make_icon


class HomePanel(QWidget):
    """Single calm control surface: Start/Pause, live gaze preview, quick switches, recent activity."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self._last_state: SessionState | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(28)

        # ---- Left column: hero state + primary controls + quick toggles
        left = QVBoxLayout()
        left.setSpacing(16)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_dot = QLabel("")
        self.status_dot.setObjectName("StatusDot")
        status_row.addWidget(self.status_dot)
        self.state_label = QLabel("Ready")
        self.state_label.setObjectName("Muted")
        status_row.addWidget(self.state_label)
        status_row.addStretch(1)
        left.addLayout(status_row)

        self.headline = QLabel("Ready to listen.")
        self.headline.setObjectName("Display")
        left.addWidget(self.headline)

        self.detail = QLabel("Look anywhere on your screens and speak to click, scroll, or type.")
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        self.detail.setMaximumWidth(520)
        left.addWidget(self.detail)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.primary = QPushButton("Start listening")
        self.primary.setObjectName("Primary")
        self.primary.clicked.connect(self.toggle)
        actions.addWidget(self.primary)

        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.clicked.connect(window.launch_calibration)
        actions.addWidget(self.calibrate_button)
        actions.addStretch(1)
        left.addLayout(actions)

        # Quick controls card (instant-apply)
        controls = Card()
        controls.layout.addWidget(eyebrow("Controls"))

        self.cursor_button = QPushButton("Show gaze dot")
        self.cursor_button.setCheckable(True)
        self.cursor_button.clicked.connect(self._toggle_cursor)

        self.transcript_button = QPushButton("Show live captions")
        self.transcript_button.setCheckable(True)
        self.transcript_button.clicked.connect(self._toggle_transcript)

        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(10)
        overlay_row.addWidget(self.cursor_button)
        overlay_row.addWidget(self.transcript_button)
        overlay_row.addStretch(1)
        controls.layout.addLayout(overlay_row)

        self.cursor_note = QLabel("")
        self.cursor_note.setObjectName("Muted")
        self.cursor_note.setWordWrap(True)
        self.cursor_note.setVisible(False)
        controls.layout.addWidget(self.cursor_note)

        self.transcript_note = QLabel("")
        self.transcript_note.setObjectName("Muted")
        self.transcript_note.setWordWrap(True)
        self.transcript_note.setVisible(False)
        controls.layout.addWidget(self.transcript_note)

        self.eye_mouse = QCheckBox("Move mouse pointer with gaze")
        self.eye_mouse.setChecked(window.config.gaze_cursor_control)
        self.eye_mouse.toggled.connect(self._toggle_eye_mouse)
        controls.layout.addWidget(self.eye_mouse)

        left.addWidget(controls)

        # Progressive disclosure: Recent activity card appears ONLY when there is activity
        self.recent_card = Card(subtle=True)
        recent_head = QHBoxLayout()
        recent_head.addWidget(eyebrow("Recent"))
        recent_head.addStretch(1)
        see_all = QPushButton("View all")
        see_all.setObjectName("Link")
        see_all.clicked.connect(lambda: window.show_page("Activity"))
        recent_head.addWidget(see_all)
        self.recent_card.layout.addLayout(recent_head)
        self.last_heard = QLabel("")
        self.last_heard.setWordWrap(True)
        self.recent_card.layout.addWidget(self.last_heard)
        self.recent_card.setVisible(False)
        left.addWidget(self.recent_card)

        left.addStretch(1)
        layout.addLayout(left, 3)

        # ---- Right column: compact live gaze preview card (hugs content cleanly)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        gaze_card = Card()
        gaze_card.setMaximumWidth(360)
        gaze_card.layout.addWidget(eyebrow("Gaze"))
        layout_info = window.display_layout
        self.live_region = calibration_region(window.paths.calibration_path, layout_info)
        self.gaze_field = GazeField(
            window,
            source_size=(layout_info.total_width, layout_info.total_height),
            live_region=self.live_region,
        )
        self.gaze_field.setFixedHeight(190)
        gaze_card.layout.addWidget(self.gaze_field)

        gaze_card.layout.addWidget(divider())
        self.hardware_summary = QLabel("")
        self.hardware_summary.setObjectName("Muted")
        self.hardware_summary.setWordWrap(True)
        gaze_card.layout.addWidget(self.hardware_summary)
        right.addWidget(gaze_card)
        right.addStretch(1)
        layout.addLayout(right, 2)

        self.gaze_field.mode_changed.connect(self._on_gaze_mode)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(1000)
        self._cursor_timer.timeout.connect(self._poll_cursor)
        self._cursor_timer.start()
        self.refresh()

    def _dot_color(self, state: SessionState) -> str:
        tokens = self.window.theme_tokens
        if state is SessionState.LISTENING:
            return tokens.success
        if state is SessionState.ERROR:
            return tokens.warning
        return tokens.muted

    def refresh(self) -> None:
        status = self.window.controller.status
        self.status_dot.setStyleSheet(f"background: {self._dot_color(status.state)}; border-radius: 5px;")

        if status.state is SessionState.LISTENING:
            self.state_label.setText("Listening")
            self.headline.setText("Listening.")
            self.detail.setText("Gaze and voice are active. Everything stays on this device.")
            self.primary.setText("Pause")
        elif status.state is SessionState.PAUSED:
            self.state_label.setText("Paused")
            self.headline.setText("Paused.")
            self.detail.setText("Camera and microphone are off.")
            self.primary.setText("Resume")
        elif status.state is SessionState.ERROR:
            self.state_label.setText("Needs attention")
            self.headline.setText("Needs attention.")
            self.detail.setText(status.detail or "Open Settings → Diagnostics for details.")
            self.primary.setText("Try again")
        else:
            calibrated = self.window.paths.calibration_path.exists()
            self.state_label.setText("Ready" if calibrated else "Needs calibration")
            self.headline.setText("Ready." if calibrated else "Calibrate to start.")
            self.detail.setText(
                "Start listening when you're ready."
                if calibrated
                else "Run a quick calibration so eye tracking knows your screen."
            )
            self.primary.setText("Start listening")

        recent = self.window.history.recent(1)
        if recent:
            item = recent[0]
            heard = str(item.get("transcript") or "").strip()
            outcome = str(item.get("target") or item.get("result") or item.get("route") or "").strip()
            summary = f"“{heard}” → {outcome}" if (heard and outcome) else (f"“{heard}”" if heard else outcome)
            self.last_heard.setText(summary)
            self.recent_card.setVisible(bool(summary))
        else:
            self.recent_card.setVisible(False)

        mode, _cam1, cam2 = resolve_camera_config(
            self.window.config.camera_mode,
            self.window.config.cam1_by_id,
            self.window.config.cam2_by_id,
        )
        calibrated = self.window.paths.calibration_path.exists()
        cams_txt = "2 cameras" if cam2 else "1 camera"
        mons = len(self.window.display_layout.monitors)
        mon_txt = f"{mons} displays" if mons > 1 else "1 display"
        cal_txt = "Calibrated" if calibrated else "Not calibrated"
        self.hardware_summary.setText(f"{cams_txt} · {mon_txt} · {cal_txt}")

        needs_calib = not self.window.paths.calibration_path.exists() and status.state is SessionState.STOPPED
        self.primary.setObjectName("" if needs_calib else "Primary")
        self.calibrate_button.setObjectName("Primary" if needs_calib else "")
        for btn in (self.primary, self.calibrate_button):
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        self.eye_mouse.blockSignals(True)
        self.eye_mouse.setChecked(self.window.config.gaze_cursor_control)
        self.eye_mouse.blockSignals(False)

        self.window.primary_action = self.primary
        self._sync_cursor_controls()
        self._sync_transcript_controls()

    def _toggle_eye_mouse(self, checked: bool) -> None:
        self.window.config.gaze_cursor_control = checked
        self.window.config.save(self.window.paths.settings_path)
        self.window.apply_live_config_change()

    def _toggle_cursor(self, checked: bool) -> None:
        if checked:
            self.live_region = calibration_region(
                self.window.paths.calibration_path, self.window.display_layout
            )
            status = self.window.screen_cursor.start(self.live_region)
            if not status.running:
                self.cursor_button.blockSignals(True)
                self.cursor_button.setChecked(False)
                self.cursor_button.blockSignals(False)
                self.cursor_note.setText(status.error or "Gaze dot unavailable on this session.")
                self.cursor_note.setVisible(True)
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
            self.cursor_note.setVisible(True)
        elif status.running:
            self._sync_cursor_controls()
        if not self.window.transcript_overlay.refresh() and self.transcript_button.isChecked():
            self._sync_transcript_controls()

    def _sync_cursor_controls(self) -> None:
        running = self.window.screen_cursor.running
        self.cursor_button.blockSignals(True)
        self.cursor_button.setChecked(running)
        self.cursor_button.blockSignals(False)
        self.cursor_button.setText("Hide gaze dot" if running else "Show gaze dot")
        if self.window.screen_cursor.error:
            self.cursor_note.setText(self.window.screen_cursor.error)
            self.cursor_note.setVisible(True)
        else:
            self.cursor_note.setVisible(False)

    def _on_gaze_mode(self, _mode: str) -> None:
        if self.window.screen_cursor.running:
            self._sync_cursor_controls()

    def _toggle_transcript(self, checked: bool) -> None:
        if checked:
            if not self.window.transcript_overlay.start():
                self.transcript_button.blockSignals(True)
                self.transcript_button.setChecked(False)
                self.transcript_button.blockSignals(False)
                self.transcript_note.setText(
                    self.window.transcript_overlay.error or "Live captions unavailable."
                )
                self.transcript_note.setVisible(True)
                return
        else:
            self.window.transcript_overlay.stop()
        self.window.config.transcript_overlay = checked
        self.window.config.save(self.window.paths.settings_path)
        self._sync_transcript_controls()

    def _sync_transcript_controls(self) -> None:
        running = self.window.transcript_overlay.running
        self.transcript_button.blockSignals(True)
        self.transcript_button.setChecked(running)
        self.transcript_button.blockSignals(False)
        self.transcript_button.setText("Hide live captions" if running else "Show live captions")
        if self.window.transcript_overlay.error and not running:
            self.transcript_note.setText(self.window.transcript_overlay.error)
            self.transcript_note.setVisible(True)
        else:
            self.transcript_note.setVisible(False)

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
        self.window.transcript_overlay.stop()

    def toggle(self) -> None:
        status = self.window.controller.status
        if status.state is SessionState.LISTENING:
            self.window.controller.pause()
        else:
            self.window.controller.start()
        self._last_state = None
        self.poll_state()


class MainWindow(QMainWindow):
    """Single-window app shell: 3 calm views + inline first-run setup."""

    PAGES = ("Home", "Activity", "Settings")

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
        self.transcript_overlay = TranscriptOverlayController(paths=self.paths)
        self.flow = flow or OnboardingFlow(self.paths)
        self.enable_previews = enable_previews
        self.report_provider = report_provider
        self.history = HistoryStore(self.paths)
        self.primary_action: QPushButton | None = None
        self.tray = None
        self.overlay = None
        self._flow_window: OnboardingWindow | None = None
        self._inline_setup: OnboardingWidget | None = None
        self._calib_proc: subprocess.Popen[bytes] | None = None
        self._display_layout = None
        self._shutdown = False

        self.setWindowTitle("Keyboard-Killer")
        self.setWindowIcon(make_icon())
        self.resize(980, 640)
        self.setMinimumSize(820, 540)

        load_bundled_fonts()
        self.theme_tokens = tokens_for(theme or ThemeName.LIGHT)

        # Root stack switches cleanly between the main 3-tab shell and inline first-run setup
        self.root_stack = QStackedWidget()
        self.setCentralWidget(self.root_stack)

        # ---- Main Shell (Index 0)
        shell = QWidget()
        row = QHBoxLayout(shell)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(196)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 20, 14, 16)
        side_layout.setSpacing(12)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        wordmark = QLabel("Keyboard-Killer")
        wordmark.setObjectName("Wordmark")
        brand.addWidget(wordmark)
        brand.addStretch(1)
        side_layout.addLayout(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav_rows: dict[str, int] = {}
        for page in self.PAGES:
            item = QListWidgetItem(page)
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.nav.addItem(item)
            self.nav_rows[page] = self.nav.count() - 1
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._select)
        side_layout.addWidget(self.nav, 1)

        self.theme_button = QPushButton("Dark mode")
        self.theme_button.setObjectName("Ghost")
        self.theme_button.clicked.connect(self.toggle_theme)
        side_layout.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignLeft)
        row.addWidget(sidebar)

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(32, 28, 32, 24)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        row.addWidget(content, 1)

        self.home = HomePanel(self)
        self.activity = HistoryPanel(self.history)
        self.settings = SettingsPanel(
            self.config,
            self.paths,
            on_config_changed=self.apply_live_config_change,
            on_calibrate=self.launch_calibration,
            on_run_setup=self.show_inline_setup,
        )
        self.pages: dict[str, QWidget] = {
            "Home": self.home,
            "Activity": self.activity,
            "Settings": self.settings,
        }
        for name in self.PAGES:
            self.stack.addWidget(self.pages[name])

        self.root_stack.addWidget(shell)

        self.set_theme(self.theme_tokens.name)
        self.refresh_tray()
        if self.config.transcript_overlay:
            self.transcript_overlay.start()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self.home.poll_state)
        self._status_timer.start()

        self._calib_timer = QTimer(self)
        self._calib_timer.setInterval(500)
        self._calib_timer.timeout.connect(self._poll_calibration)

    @property
    def display_layout(self):
        if self._display_layout is None:
            self._display_layout = get_display_layout()
        return self._display_layout

    def apply_live_config_change(self) -> None:
        self.config = AppConfig.load(self.paths.settings_path)
        self.home.refresh()
        if self.controller.status.state is SessionState.LISTENING:
            self.controller.pause()
            self.controller.start()
            self.home.poll_state()

    def launch_calibration(self) -> None:
        if self._calib_proc is not None and self._calib_proc.poll() is None:
            return
        was_listening = self.controller.status.state is SessionState.LISTENING
        if was_listening:
            self.controller.pause()
        self._resume_after_calib = was_listening
        try:
            self._calib_proc = subprocess.Popen(
                [sys.executable, "-m", "keyboard_killer.main", "calibrate"]
            )
            self._calib_timer.start()
        except OSError:
            self._calib_proc = None

    def _poll_calibration(self) -> None:
        if self._calib_proc is None or self._calib_proc.poll() is not None:
            self._calib_timer.stop()
            self._calib_proc = None
            self.home.live_region = calibration_region(
                self.paths.calibration_path, self.display_layout
            )
            self.home.refresh()
            self.settings.refresh()
            if getattr(self, "_resume_after_calib", False):
                self._resume_after_calib = False
                self.controller.start()
                self.home.poll_state()

    def show_inline_setup(self) -> OnboardingWidget:
        """Show the distraction-free setup flow inline inside this single window."""
        if self._inline_setup is not None:
            self.root_stack.removeWidget(self._inline_setup)
            self._inline_setup.close_resources()
            self._inline_setup.deleteLater()
        if self.flow.is_complete:
            self.flow.reset()
        widget = OnboardingWidget(
            flow=self.flow,
            config=self.config,
            paths=self.paths,
            report_provider=self.report_provider,
            enable_previews=self.enable_previews,
            parent=self,
        )
        widget.completed.connect(self._on_inline_setup_complete)
        self._inline_setup = widget
        self.root_stack.addWidget(widget)
        self.root_stack.setCurrentWidget(widget)
        return widget

    def _on_inline_setup_complete(self) -> None:
        self.config = AppConfig.load(self.paths.settings_path)
        self.root_stack.setCurrentIndex(0)
        self.show_page("Home")
        self.settings.refresh()
        self.home.refresh()
        if self.paths.calibration_path.exists() and self.controller.status.state is not SessionState.LISTENING:
            self.controller.start()
            self.home.poll_state()

    def open_onboarding(self) -> OnboardingWindow:
        """Open setup in a standalone dialog (kept for external callers/tests)."""
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
        self.settings.refresh()
        self.home.refresh()
        self.flow = self._flow_window.flow if self._flow_window else self.flow

    def set_theme(self, theme: ThemeName | str) -> None:
        self.theme_tokens = tokens_for(theme)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(self.theme_tokens))
        else:
            self.setStyleSheet(stylesheet(self.theme_tokens))
        if hasattr(self, "theme_button"):
            self.theme_button.setText(
                "Light mode" if self.theme_tokens.name is ThemeName.DARK else "Dark mode"
            )
        if hasattr(self, "home"):
            self.home.refresh()

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
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()

    def show_page(self, name: str) -> None:
        # Backwards-compatible alias for older callers referencing "History"
        target = "Activity" if name == "History" else name
        if target in self.pages:
            self.root_stack.setCurrentIndex(0)
            row = self.nav_rows.get(target, 0)
            self.nav.setCurrentRow(row)
            self._select(row)

    def refresh_tray(self) -> None:
        if self.tray is not None:
            status = self.controller.status
            self.tray.set_state(status.state, status.detail)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._status_timer.stop()
        self._calib_timer.stop()
        if self._inline_setup is not None:
            self._inline_setup.close_resources()
        self.settings.stop()
        self.home.shutdown()
        self.transcript_overlay.stop()
        self.controller.quit()

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
    app.setWindowIcon(make_icon())
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
        on_calibrate=window.launch_calibration,
    )
    if tray.start():
        window.tray = tray
    window.overlay = create_overlay(tray=tray.icon, env=None)
    # First launch embeds setup directly in the same window — never stacks two windows!
    if not window.flow.is_complete:
        window.show_inline_setup()
    window.show()
    return app.exec()
