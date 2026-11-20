"""Small settings surface; every change writes the same AppConfig as the CLI."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.audio.quick_test import (
    MicLevel,
    check_microphone,
    input_devices,
)
from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.platform.linux.cameras import resolve_camera_config
from keyboard_killer.ui.components import Card, eyebrow
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.models.providers.presets import load_presets
from keyboard_killer.models.providers.secrets import keyring_available
from keyboard_killer.platform.capabilities import detect_capabilities


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


class SettingsPanel(QWidget):
    def __init__(
        self,
        config: AppConfig | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths or current_paths()
        self.config = config or AppConfig.load(self.paths.settings_path)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(content)
        root.addWidget(scroll)

        title = QLabel("Settings")
        title.setObjectName("Heading")
        layout.addWidget(title)
        subtitle = QLabel("Changes save to settings.json; API keys stay in the OS keychain.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(10)

        form.addRow(eyebrow("INPUT"))
        self.hotkey = QLineEdit(self.config.ptt_hotkey)
        form.addRow("Push-to-talk key", self.hotkey)

        self.always_on = QCheckBox("Listen continuously (voice activity)")
        self.always_on.setChecked(self.config.always_on_voice)
        form.addRow("", self.always_on)

        self.microphone = QComboBox()
        self.microphone.setMinimumWidth(260)
        self._populate_microphones()
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.microphone, 1)
        self.mic_test = QPushButton("CHECK MIC")
        self.mic_test.clicked.connect(self.check_mic)
        mic_row.addWidget(self.mic_test)
        form.addRow("Microphone", mic_row)
        self.mic_result = QLabel(
            "Check the input, then Save. Pause and resume the daemon to apply it."
        )
        self.mic_result.setObjectName("Muted")
        self.mic_result.setWordWrap(True)
        form.addRow("", self.mic_result)
        self._mic_worker: MicCheckWorker | None = None

        self.require_prefix = QCheckBox("Only act on command phrases")
        self.require_prefix.setChecked(self.config.require_command_prefix)
        form.addRow("", self.require_prefix)

        self.pop_click = QCheckBox("Enable pop-click")
        self.pop_click.setChecked(self.config.enable_pop_click)
        form.addRow("", self.pop_click)

        self.eye_mouse = QCheckBox("Let gaze move the pointer")
        self.eye_mouse.setChecked(self.config.gaze_cursor_control)
        form.addRow("", self.eye_mouse)

        form.addRow(eyebrow("CAMERAS + SPEECH"))
        mode, cam1, cam2 = resolve_camera_config(
            self.config.camera_mode, self.config.cam1_by_id, self.config.cam2_by_id
        )
        self.camera_mode = QLabel(f"{mode} · {'2 cameras' if cam2 else '1 camera'}")
        self.camera_mode.setObjectName("Muted")
        form.addRow("Camera mode", self.camera_mode)
        self.stt_status = QLabel("")
        self.stt_status.setObjectName("Muted")
        form.addRow("Speech model", self.stt_status)

        form.addRow(eyebrow("OPTIONAL AI"))
        self.provider_preset = QComboBox()
        for preset in load_presets():
            self.provider_preset.addItem(preset.name, preset.id)
        index = self.provider_preset.findData(self.config.llm_preset)
        if index >= 0:
            self.provider_preset.setCurrentIndex(index)
        form.addRow("AI provider", self.provider_preset)

        self.llm_model = QLineEdit(self.config.llm_model)
        self.llm_model.setPlaceholderText("Provider default")
        form.addRow("AI model", self.llm_model)

        self.vlm_consent = QCheckBox("Allow screenshots to leave this device for the gaze VLM")
        self.vlm_consent.setChecked(self.config.gaze_vlm_remote_consent)
        form.addRow("", self.vlm_consent)

        from keyboard_killer.voice.stt.factory import stt_report

        report = stt_report(self.config, paths=self.paths)
        state = report.get("parakeet", {}).get("state", "missing")
        self.stt_status.setText(f"Parakeet · {state} · CPU int8 by default")

        form.addRow(eyebrow("SYSTEM"))
        self.keyring_status = QLabel(
            "OS keychain available." if keyring_available() else "OS keychain unavailable."
        )
        self.keyring_status.setObjectName("Muted")
        form.addRow("API keys", self.keyring_status)

        card = Card()
        card.layout.addLayout(form)
        layout.addWidget(card)

        capabilities = detect_capabilities(paths=self.paths)
        overlay = capabilities.get("overlay")
        overlay_text = (
            f"Overlay: {overlay.status.value if overlay else 'unknown'} "
            f"({overlay.detail if overlay else 'not probed'})"
        )
        overlay_label = QLabel(overlay_text)
        overlay_label.setObjectName("Muted")
        overlay_label.setWordWrap(True)
        layout.addWidget(overlay_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        layout.addLayout(actions)
        self.saved = QLabel("")
        self.saved.setObjectName("Success")
        layout.addWidget(self.saved)
        layout.addStretch(1)

    def _populate_microphones(self) -> None:
        self.microphone.clear()
        self.microphone.addItem("System default", None)
        for device in input_devices():
            self.microphone.addItem(device.name, device.index)
        current = self.config.audio_device
        if current is None:
            return
        index = self.microphone.findData(current)
        if index < 0 and isinstance(current, str):
            index = self.microphone.findText(current)
        if index >= 0:
            self.microphone.setCurrentIndex(index)

    def check_mic(self) -> None:
        if self._mic_worker is not None and self._mic_worker.isRunning():
            return
        self.mic_test.setEnabled(False)
        self.mic_result.setObjectName("Muted")
        self.mic_result.setText("LISTENING · 1.5 SECONDS")
        worker = MicCheckWorker(self.microphone.currentData())
        worker.done.connect(self._on_mic_done)
        worker.failed.connect(self._on_mic_failed)
        self._mic_worker = worker
        worker.start()

    def _on_mic_done(self, level: MicLevel) -> None:
        self.mic_test.setEnabled(True)
        self.mic_result.setObjectName("Success" if level.heard else "Warning")
        self.mic_result.setText(
            (
                f"HEARD · PEAK {level.peak:.3f} · RMS {level.rms:.3f}"
                if level.heard
                else "NO SIGNAL · TRY ANOTHER MICROPHONE"
            )
        )
        self.mic_result.style().unpolish(self.mic_result)
        self.mic_result.style().polish(self.mic_result)

    def _on_mic_failed(self, message: str) -> None:
        self.mic_test.setEnabled(True)
        self.mic_result.setObjectName("Warning")
        self.mic_result.setText(message)
        self.mic_result.style().unpolish(self.mic_result)
        self.mic_result.style().polish(self.mic_result)

    def stop(self) -> None:
        if self._mic_worker is not None and self._mic_worker.isRunning():
            self._mic_worker.wait(3000)

    def save(self) -> None:
        cfg = self.config
        cfg.ptt_hotkey = self.hotkey.text().strip() or "KEY_RIGHTCTRL"
        cfg.always_on_voice = self.always_on.isChecked()
        cfg.audio_device = self.microphone.currentData()
        cfg.require_command_prefix = self.require_prefix.isChecked()
        cfg.enable_pop_click = self.pop_click.isChecked()
        cfg.gaze_cursor_control = self.eye_mouse.isChecked()
        cfg.llm_preset = str(self.provider_preset.currentData() or "openai")
        cfg.llm_provider = "openai_compat"
        cfg.llm_model = self.llm_model.text().strip()
        cfg.gaze_vlm_remote_consent = self.vlm_consent.isChecked()
        cfg.save(self.paths.settings_path)
        self.saved.setText(f"Saved to {self.paths.settings_path}")
