"""Calm, instant-apply Settings surface with progressive enhancement."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from keyless.audio.quick_test import (
    DEFAULT_SECONDS,
    MicLevel,
    SpeechTestResult,
    check_microphone,
    input_devices,
    run_speech_test,
)
from keyless.config.paths import AppPaths, current_paths
from keyless.config.schema import AppConfig
from keyless.diagnostics.doctor import collect_doctor, format_text
from keyless.models.catalog import get_asset
from keyless.models.downloader import download_asset
from keyless.models.providers.acp_registry import fetch_registry
from keyless.models.providers.presets import load_presets
from keyless.models.providers.secrets import keyring_available, set_secret
from keyless.platform.linux.cameras import (
    discover_v4l_by_id,
    friendly_camera_name,
    resolve_camera_config,
)
from keyless.platform.linux.pipewire import pipewire_available
from keyless.ui.components import (
    Card,
    DisclosureSection,
    KeyCaptureButton,
    divider,
    eyebrow,
)
from keyless.voice.stt.factory import stt_report

VERSION = "0.1.0"


class MicCheckWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, device: int | str | None) -> None:
        super().__init__()
        self.device = device

    def run(self) -> None:
        try:
            self.done.emit(check_microphone(self.device))
        except Exception as exc:
            self.failed.emit(str(exc))


class _SpeechCheckWorker(QThread):
    stage = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, config: AppConfig, paths: AppPaths) -> None:
        super().__init__()
        self.config = config
        self.paths = paths

    def run(self) -> None:
        try:
            self.done.emit(
                run_speech_test(
                    self.config,
                    paths=self.paths,
                    seconds=DEFAULT_SECONDS,
                    on_stage=self.stage.emit,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class _ModelDownloadWorker(QThread):
    progress = Signal(object)
    done = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            asset = get_asset("parakeet-tdt-0.6b-v2")
            self.done.emit(download_asset(asset, progress=lambda p: self.progress.emit(p)))
        except Exception as exc:
            self.failed.emit(str(exc))


class SettingsPanel(QWidget):
    """Instant-apply Settings: Voice, Camera & Gaze, Optional AI, and collapsible Diagnostics."""

    def __init__(
        self,
        config: AppConfig | None = None,
        paths: AppPaths | None = None,
        *,
        on_config_changed: Callable[[], None] | None = None,
        on_calibrate: Callable[[], None] | None = None,
        on_run_setup: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths or current_paths()
        self.config = config or AppConfig.load(self.paths.settings_path)
        self._on_config_changed = on_config_changed
        self._on_calibrate = on_calibrate
        self._on_run_setup = on_run_setup
        self._loading = True
        self._mic_worker: MicCheckWorker | None = None
        self._speech_worker: _SpeechCheckWorker | None = None
        self._dl_worker: _ModelDownloadWorker | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 16)
        layout.setSpacing(16)
        scroll.setWidget(content)
        root.addWidget(scroll)

        title = QLabel("Settings")
        title.setObjectName("Title")
        layout.addWidget(title)

        # ------------------------------------------------------------------ Voice
        voice_card = Card()
        voice_card.layout.addWidget(eyebrow("Voice"))
        vform = QFormLayout()
        vform.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        vform.setVerticalSpacing(10)

        self.microphone = QComboBox()
        self.microphone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._populate_microphones()
        self.microphone.currentIndexChanged.connect(self._auto_save)
        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        mic_row.addWidget(self.microphone, 1)
        self.mic_test = QPushButton("Check mic")
        self.mic_test.clicked.connect(self.check_mic)
        mic_row.addWidget(self.mic_test)
        self.speech_test = QPushButton("Test speech")
        self.speech_test.clicked.connect(self.test_speech)
        mic_row.addWidget(self.speech_test)
        vform.addRow("Microphone", mic_row)

        self.mic_result = QLabel("")
        self.mic_result.setObjectName("Muted")
        self.mic_result.setWordWrap(True)
        self.mic_result.setVisible(False)
        vform.addRow("", self.mic_result)

        self.always_on = QCheckBox("Always listen for voice commands")
        self.always_on.setChecked(self.config.always_on_voice)
        self.always_on.toggled.connect(self._on_always_on_toggled)
        vform.addRow("", self.always_on)

        self.hotkey_label = QLabel("Push-to-talk key")
        self.hotkey = KeyCaptureButton(self.config.ptt_hotkey)
        self.hotkey.key_changed.connect(self._auto_save)
        vform.addRow(self.hotkey_label, self.hotkey)

        self.require_prefix = QCheckBox("Only act on command phrases (click, scroll, type…)")
        self.require_prefix.setChecked(self.config.require_command_prefix)
        self.require_prefix.toggled.connect(self._auto_save)
        vform.addRow("", self.require_prefix)

        self.pop_click = QCheckBox("Pop-click (lip pop triggers left click)")
        self.pop_click.setChecked(self.config.enable_pop_click)
        self.pop_click.toggled.connect(self._auto_save)
        vform.addRow("", self.pop_click)

        voice_card.layout.addLayout(vform)
        voice_card.layout.addWidget(divider())

        stt_row = QHBoxLayout()
        self.stt_status = QLabel("")
        self.stt_status.setObjectName("Muted")
        stt_row.addWidget(self.stt_status, 1)
        self.dl_button = QPushButton("Download speech model (630 MB)")
        self.dl_button.clicked.connect(self._download_speech_model)
        stt_row.addWidget(self.dl_button)
        voice_card.layout.addLayout(stt_row)

        self.dl_progress = QProgressBar()
        self.dl_progress.setRange(0, 100)
        self.dl_progress.setFixedHeight(4)
        self.dl_progress.setTextVisible(False)
        self.dl_progress.setVisible(False)
        voice_card.layout.addWidget(self.dl_progress)
        layout.addWidget(voice_card)

        # ------------------------------------------------------------------ Camera & Gaze
        gaze_card = Card()
        gaze_card.layout.addWidget(eyebrow("Camera & gaze"))
        gform = QFormLayout()
        gform.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        gform.setVerticalSpacing(10)

        self.cam_mode_label = QLabel("Camera setup")
        self.cam_mode = QComboBox()
        self.cam_mode.addItem("Automatic", "auto")
        self.cam_mode.addItem("One camera", "mono")
        self.cam_mode.addItem("Two cameras (stereo)", "stereo")
        m_idx = self.cam_mode.findData(self.config.camera_mode)
        self.cam_mode.setCurrentIndex(m_idx if m_idx >= 0 else 0)
        self.cam_mode.currentIndexChanged.connect(self._on_camera_setting_changed)
        gform.addRow(self.cam_mode_label, self.cam_mode)

        self.cam1 = QComboBox()
        self.cam1.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cam1.currentIndexChanged.connect(self._auto_save)
        gform.addRow("Camera", self.cam1)

        self.cam2_label = QLabel("Second camera")
        self.cam2 = QComboBox()
        self.cam2.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cam2.currentIndexChanged.connect(self._auto_save)
        gform.addRow(self.cam2_label, self.cam2)

        self.eye_mouse = QCheckBox("Move the mouse pointer with gaze")
        self.eye_mouse.setChecked(self.config.gaze_cursor_control)
        self.eye_mouse.toggled.connect(self._auto_save)
        gform.addRow("", self.eye_mouse)

        self.online_adapt = QCheckBox("Automatically adapt to posture and camera drift")
        self.online_adapt.setChecked(
            bool(getattr(self.config, "gaze_online_ekf", True))
        )
        self.online_adapt.toggled.connect(self._auto_save)
        gform.addRow("", self.online_adapt)

        self.share_camera = QCheckBox("Share camera with video calls and other apps")
        self.share_camera.setChecked(self.config.camera_backend == "pipewire")
        self.share_camera.toggled.connect(self._auto_save)
        gform.addRow("", self.share_camera)

        gaze_card.layout.addLayout(gform)

        calib_row = QHBoxLayout()
        self.calib_status = QLabel("")
        self.calib_status.setObjectName("Muted")
        calib_row.addWidget(self.calib_status, 1)
        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.clicked.connect(self._trigger_calibrate)
        calib_row.addWidget(self.calibrate_button)
        gaze_card.layout.addLayout(calib_row)
        layout.addWidget(gaze_card)

        # ------------------------------------------------------------------ Optional AI
        ai_card = Card()
        ai_card.layout.addWidget(eyebrow("AI assistant (optional)"))
        aform = QFormLayout()
        aform.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        aform.setVerticalSpacing(10)

        self.provider_kind = QComboBox()
        self.provider_kind.addItem("Automatic", "auto")
        self.provider_kind.addItem("API provider", "openai_compat")
        self.provider_kind.addItem("Local ACP agent", "acp")
        p_idx = self.provider_kind.findData(self.config.llm_provider)
        self.provider_kind.setCurrentIndex(p_idx if p_idx >= 0 else 0)
        self.provider_kind.currentIndexChanged.connect(self._on_ai_kind_changed)
        aform.addRow("Backend", self.provider_kind)

        self.provider_preset_label = QLabel("Provider")
        self.provider_preset = QComboBox()
        for preset in load_presets():
            self.provider_preset.addItem(preset.name, preset.id)
        index = self.provider_preset.findData(self.config.llm_preset)
        if index >= 0:
            self.provider_preset.setCurrentIndex(index)
        self.provider_preset.currentIndexChanged.connect(self._auto_save)
        aform.addRow(self.provider_preset_label, self.provider_preset)

        self.acp_label = QLabel("ACP agent")
        self.acp_agent = QLineEdit(self.config.acp_agent_id)
        self.acp_agent.setPlaceholderText("e.g. opencode")
        self.acp_agent.editingFinished.connect(self._auto_save)
        aform.addRow(self.acp_label, self.acp_agent)

        self.llm_model_label = QLabel("Model")
        self.llm_model = QLineEdit(self.config.llm_model)
        self.llm_model.setPlaceholderText("Provider default")
        self.llm_model.editingFinished.connect(self._auto_save)
        aform.addRow(self.llm_model_label, self.llm_model)

        self.api_key_label = QLabel("API key")
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(
            "Stored in OS keychain" if keyring_available() else "OS keychain unavailable"
        )
        self.api_key.setEnabled(keyring_available())
        key_row.addWidget(self.api_key, 1)
        self.save_key_button = QPushButton("Store key")
        self.save_key_button.setEnabled(keyring_available())
        self.save_key_button.clicked.connect(self._store_api_key)
        key_row.addWidget(self.save_key_button)
        aform.addRow(self.api_key_label, key_row)

        self.vlm_consent = QCheckBox("Allow sending screen crops to remote AI for visual clicking")
        self.vlm_consent.setChecked(self.config.gaze_vlm_remote_consent)
        self.vlm_consent.toggled.connect(self._auto_save)
        aform.addRow("", self.vlm_consent)

        self.ai_note = QLabel("")
        self.ai_note.setObjectName("Muted")
        self.ai_note.setVisible(False)
        aform.addRow("", self.ai_note)

        ai_card.layout.addLayout(aform)
        layout.addWidget(ai_card)

        # ------------------------------------------------------------------ Collapsible Diagnostics & About
        self.diagnostics = DisclosureSection(
            "Diagnostics & about", on_expand=self.refresh_diagnostics
        )
        diag_card = Card(subtle=True)
        self._diag_grid = QGridLayout()
        self._diag_grid.setVerticalSpacing(6)
        self._diag_badges: dict[str, QLabel] = {}
        for row_i, (key, label) in enumerate(
            (
                ("input", "Input"),
                ("cameras", "Cameras"),
                ("screen", "Screen"),
                ("speech", "Speech"),
                ("ai", "AI"),
                ("overlay", "Overlay"),
            )
        ):
            self._diag_grid.addWidget(QLabel(label), row_i, 0)
            val = QLabel("—")
            val.setObjectName("Muted")
            self._diag_grid.addWidget(val, row_i, 1)
            self._diag_badges[key] = val
        self._diag_grid.setColumnStretch(1, 1)
        diag_card.layout.addLayout(self._diag_grid)

        self.diag_text = QTextEdit()
        self.diag_text.setReadOnly(True)
        self.diag_text.setFixedHeight(170)
        diag_card.layout.addWidget(self.diag_text)

        diag_actions = QHBoxLayout()
        copy_diag = QPushButton("Copy report")
        copy_diag.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.diag_text.toPlainText()))
        diag_actions.addWidget(copy_diag)
        rerun = QPushButton("Run setup again")
        rerun.clicked.connect(self._trigger_setup)
        diag_actions.addWidget(rerun)
        diag_actions.addStretch(1)
        about = QLabel(f"Keyless {VERSION} · Local camera & speech")
        about.setObjectName("Muted")
        diag_actions.addWidget(about)
        diag_card.layout.addLayout(diag_actions)

        self.diagnostics.body_layout.addWidget(diag_card)
        layout.addWidget(self.diagnostics)
        layout.addStretch(1)

        self._loading = False
        self.refresh()

    # ------------------------------------------------------------------ Helpers

    def _populate_microphones(self) -> None:
        self.microphone.blockSignals(True)
        self.microphone.clear()
        self.microphone.addItem("System default", None)
        for device in input_devices():
            self.microphone.addItem(device.name, device.index)
        current = self.config.audio_device
        if current is not None:
            index = self.microphone.findData(current)
            if index < 0 and isinstance(current, str):
                index = self.microphone.findText(current)
            if index >= 0:
                self.microphone.setCurrentIndex(index)
        self.microphone.blockSignals(False)

    def _refresh_cameras(self) -> None:
        devices = discover_v4l_by_id()
        has_two = len(devices) >= 2
        mode, cam1, cam2 = resolve_camera_config(
            self.config.camera_mode, self.config.cam1_by_id, self.config.cam2_by_id
        )
        show_stereo = has_two and self.cam_mode.currentData() != "mono"
        self.cam_mode_label.setVisible(has_two)
        self.cam_mode.setVisible(has_two)
        self.cam2_label.setVisible(show_stereo)
        self.cam2.setVisible(show_stereo)

        for combo, current in ((self.cam1, cam1), (self.cam2, cam2)):
            combo.blockSignals(True)
            combo.clear()
            if not devices and not current:
                combo.addItem("No camera detected", "")
            for dev in devices:
                combo.addItem(friendly_camera_name(dev), dev)
            idx = combo.findData(current)
            if idx < 0 and current:
                combo.addItem(friendly_camera_name(current), current)
                idx = combo.count() - 1
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

        can_share = pipewire_available() and mode == "mono"
        self.share_camera.setEnabled(can_share)
        self.share_camera.setToolTip(
            "" if can_share else "Camera sharing is available in single-camera mode with PipeWire."
        )

    def refresh(self) -> None:
        self._loading = True
        self._refresh_cameras()
        self._sync_hotkey_visibility()
        self._sync_ai_visibility()

        report = stt_report(self.config, paths=self.paths)
        ready = report.get("selected") == "parakeet"
        self.stt_status.setText("Speech model ready (offline)" if ready else "Speech model not downloaded")
        self.dl_button.setVisible(not ready)
        self.speech_test.setEnabled(ready)

        calibrated = self.paths.calibration_path.exists()
        self.calib_status.setText("Eye tracking calibrated" if calibrated else "Not calibrated yet")
        self.calibrate_button.setText("Recalibrate" if calibrated else "Calibrate")
        self._loading = False

    def _sync_hotkey_visibility(self) -> None:
        # Progressive disclosure: hide push-to-talk shortcut when always-on is enabled
        show_ptt = not self.always_on.isChecked()
        self.hotkey_label.setVisible(show_ptt)
        self.hotkey.setVisible(show_ptt)

    def _sync_ai_visibility(self) -> None:
        kind = str(self.provider_kind.currentData() or "auto")
        is_api = kind == "openai_compat"
        is_acp = kind == "acp"
        self.provider_preset_label.setVisible(is_api)
        self.provider_preset.setVisible(is_api)
        self.api_key_label.setVisible(is_api)
        self.api_key.setVisible(is_api)
        self.save_key_button.setVisible(is_api)
        self.llm_model_label.setVisible(is_api)
        self.llm_model.setVisible(is_api)
        self.acp_label.setVisible(is_acp)
        self.acp_agent.setVisible(is_acp)
        if is_acp:
            try:
                installed = [s.id for s in fetch_registry() if s.installed]
                if installed and not self.acp_agent.text().strip():
                    self.acp_agent.setText(installed[0])
            except Exception:
                pass

    def _on_always_on_toggled(self) -> None:
        self._sync_hotkey_visibility()
        self._auto_save()

    def _on_camera_setting_changed(self) -> None:
        self._refresh_cameras()
        self._auto_save()

    def _on_ai_kind_changed(self) -> None:
        self._sync_ai_visibility()
        self._auto_save()

    def _store_api_key(self) -> None:
        key = self.api_key.text().strip()
        if not key:
            return
        preset = str(self.provider_preset.currentData() or "openai")
        set_secret(preset, key)
        self.api_key.clear()
        self.ai_note.setText("Key saved securely in the OS keychain.")
        self.ai_note.setObjectName("Success")
        self.ai_note.setVisible(True)
        self._auto_save()

    def _trigger_calibrate(self) -> None:
        if self._on_calibrate is not None:
            self._on_calibrate()

    def _trigger_setup(self) -> None:
        if self._on_run_setup is not None:
            self._on_run_setup()

    # ------------------------------------------------------------------ Audio checks

    def check_mic(self) -> None:
        if self._mic_worker is not None and self._mic_worker.isRunning():
            return
        self.mic_test.setEnabled(False)
        self.mic_result.setObjectName("Muted")
        self.mic_result.setText("Listening for 1.5 seconds…")
        self.mic_result.setVisible(True)
        worker = MicCheckWorker(self.microphone.currentData())
        worker.done.connect(self._on_mic_done)
        worker.failed.connect(self._on_mic_failed)
        self._mic_worker = worker
        worker.start()

    def _on_mic_done(self, level: MicLevel) -> None:
        self.mic_test.setEnabled(True)
        self.mic_result.setObjectName("Success" if level.heard else "Warning")
        self.mic_result.setText(
            f"Microphone heard you clearly (peak {level.peak:.2f})."
            if level.heard
            else "No audio signal — try another microphone."
        )
        self.mic_result.setVisible(True)
        self.mic_result.style().unpolish(self.mic_result)
        self.mic_result.style().polish(self.mic_result)

    def _on_mic_failed(self, message: str) -> None:
        self.mic_test.setEnabled(True)
        self.speech_test.setEnabled(True)
        self.mic_result.setObjectName("Warning")
        self.mic_result.setText(message)
        self.mic_result.setVisible(True)

    def test_speech(self) -> None:
        if self._speech_worker is not None and self._speech_worker.isRunning():
            return
        self.speech_test.setEnabled(False)
        self.mic_result.setObjectName("Muted")
        self.mic_result.setText("Loading speech engine…")
        self.mic_result.setVisible(True)
        worker = _SpeechCheckWorker(self.config, self.paths)
        worker.stage.connect(
            lambda s: self.mic_result.setText(
                "Speak now (4 seconds)…" if s == "recording" else "Transcribing…" if s == "transcribing" else "Loading speech engine…"
            )
        )
        worker.done.connect(self._on_speech_done)
        worker.failed.connect(self._on_mic_failed)
        self._speech_worker = worker
        worker.start()

    def _on_speech_done(self, result: SpeechTestResult) -> None:
        self.speech_test.setEnabled(True)
        self.mic_result.setObjectName("Success" if result.has_speech else "Warning")
        self.mic_result.setText(
            f"Heard: “{result.transcript}” ({result.latency_ms:.0f} ms)"
            if result.transcript
            else "No words recognized."
        )
        self.mic_result.setVisible(True)
        self.mic_result.style().unpolish(self.mic_result)
        self.mic_result.style().polish(self.mic_result)

    def _download_speech_model(self) -> None:
        if self._dl_worker is not None and self._dl_worker.isRunning():
            return
        self.dl_button.setEnabled(False)
        self.dl_button.setText("Downloading…")
        self.dl_progress.setVisible(True)
        self.dl_progress.setValue(0)
        worker = _ModelDownloadWorker(self)
        worker.progress.connect(lambda p: self.dl_progress.setValue(int(p.percent)))
        worker.done.connect(lambda _r: self._finish_dl(""))
        worker.failed.connect(self._finish_dl)
        self._dl_worker = worker
        worker.start()

    def _finish_dl(self, err: str) -> None:
        self._dl_worker = None
        self.dl_progress.setVisible(False)
        self.dl_button.setEnabled(True)
        self.dl_button.setText("Retry download" if err else "Download speech model (630 MB)")
        if err:
            self.stt_status.setText(f"Download failed: {err}")
        else:
            self.refresh()

    # ------------------------------------------------------------------ Diagnostics

    def refresh_diagnostics(self) -> None:
        try:
            report = collect_doctor(check_providers=False)
            caps = report.capabilities.get("capabilities", {})
            mapping = {
                "input": (caps.get("pointer", {}).get("status", "unknown"), caps.get("pointer", {}).get("status") == "available"),
                "cameras": (report.cameras.get("mode", "unknown"), report.cameras.get("mode") in ("mono", "stereo")),
                "screen": (caps.get("screen_capture", {}).get("status", "unknown"), caps.get("screen_capture", {}).get("status") == "available"),
                "speech": (report.stt.get("selected", "missing"), report.stt.get("selected") == "parakeet"),
                "ai": (report.providers.get("text_llm", {}).get("status", {}).get("state", "optional"), None),
                "overlay": (caps.get("overlay", {}).get("status", "unknown"), caps.get("overlay", {}).get("status") == "available"),
            }
            for key, (val, ok) in mapping.items():
                lbl = self._diag_badges[key]
                lbl.setText(str(val).replace("_", " ").capitalize())
                lbl.setObjectName("Success" if ok is True else "Warning" if ok is False else "Muted")
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
            self.diag_text.setPlainText(format_text(report))
        except Exception as exc:
            self.diag_text.setPlainText(f"Diagnostics unavailable: {exc}")

    # ------------------------------------------------------------------ Persistence

    def _auto_save(self, *_args: object) -> None:
        if self._loading:
            return
        self.save()

    def save(self) -> None:
        cfg = self.config
        cfg.ptt_hotkey = self.hotkey.text().strip() or "KEY_RIGHTCTRL"
        cfg.always_on_voice = self.always_on.isChecked()
        cfg.audio_device = self.microphone.currentData()
        cfg.require_command_prefix = self.require_prefix.isChecked()
        cfg.enable_pop_click = self.pop_click.isChecked()
        cfg.gaze_cursor_control = self.eye_mouse.isChecked()
        cfg.gaze_online_ekf = self.online_adapt.isChecked()
        cfg.gaze_online_chase = self.online_adapt.isChecked()
        cfg.camera_backend = "pipewire" if self.share_camera.isChecked() else "v4l2"
        if self.cam_mode.isVisible():
            cfg.camera_mode = str(self.cam_mode.currentData() or "auto")
        if self.cam1.currentData():
            cfg.cam1_by_id = str(self.cam1.currentData())
        if self.cam2.isVisible() and self.cam2.currentData():
            cfg.cam2_by_id = str(self.cam2.currentData())
        cfg.llm_provider = str(self.provider_kind.currentData() or "auto")
        cfg.llm_preset = str(self.provider_preset.currentData() or "openai")
        cfg.llm_model = self.llm_model.text().strip()
        cfg.acp_agent_id = self.acp_agent.text().strip() or "opencode"
        cfg.gaze_vlm_remote_consent = self.vlm_consent.isChecked()
        cfg.save(self.paths.settings_path)
        if self._on_config_changed is not None:
            self._on_config_changed()

    def stop(self) -> None:
        for worker in (self._mic_worker, self._speech_worker, self._dl_worker):
            if worker is not None and worker.isRunning():
                worker.wait(3000)
