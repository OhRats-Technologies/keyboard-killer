"""Small settings surface; every change writes the same AppConfig as the CLI."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.models.providers.presets import load_presets
from keyboard_killer.models.providers.secrets import keyring_available
from keyboard_killer.platform.capabilities import detect_capabilities


class SettingsPanel(QWidget):
    def __init__(
        self,
        config: AppConfig | None = None,
        paths: AppPaths | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths or current_paths()
        self.config = config or AppConfig.load(self.paths.settings_path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("Settings")
        title.setObjectName("Heading")
        layout.addWidget(title)
        subtitle = QLabel("Changes save to settings.json; API keys stay in the OS keychain.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.hotkey = QLineEdit(self.config.ptt_hotkey)
        form.addRow("Push-to-talk key", self.hotkey)

        self.always_on = QCheckBox("Listen continuously (voice activity)")
        self.always_on.setChecked(self.config.always_on_voice)
        form.addRow("", self.always_on)

        self.require_prefix = QCheckBox("Only act on command phrases")
        self.require_prefix.setChecked(self.config.require_command_prefix)
        form.addRow("", self.require_prefix)

        self.pop_click = QCheckBox("Enable pop-click")
        self.pop_click.setChecked(self.config.enable_pop_click)
        form.addRow("", self.pop_click)

        self.eye_mouse = QCheckBox("Let gaze move the pointer")
        self.eye_mouse.setChecked(self.config.gaze_cursor_control)
        form.addRow("", self.eye_mouse)

        self.stt_engine = QComboBox()
        self.stt_engine.addItems(["auto", "parakeet", "whisper"])
        self.stt_engine.setCurrentText(self.config.stt_engine)
        form.addRow("Speech-to-text", self.stt_engine)

        self.whisper_model = QLineEdit(self.config.whisper_model)
        form.addRow("Whisper fallback", self.whisper_model)

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

        self.keyring_status = QLabel(
            "OS keychain available." if keyring_available() else "OS keychain unavailable."
        )
        self.keyring_status.setObjectName("Muted")
        form.addRow("API keys", self.keyring_status)

        layout.addLayout(form)

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

    def save(self) -> None:
        cfg = self.config
        cfg.ptt_hotkey = self.hotkey.text().strip() or "KEY_RIGHTCTRL"
        cfg.always_on_voice = self.always_on.isChecked()
        cfg.require_command_prefix = self.require_prefix.isChecked()
        cfg.enable_pop_click = self.pop_click.isChecked()
        cfg.gaze_cursor_control = self.eye_mouse.isChecked()
        cfg.stt_engine = self.stt_engine.currentText()
        cfg.whisper_model = self.whisper_model.text().strip() or "base.en"
        cfg.llm_preset = str(self.provider_preset.currentData() or "openai")
        cfg.llm_provider = "openai_compat"
        cfg.llm_model = self.llm_model.text().strip()
        cfg.gaze_vlm_remote_consent = self.vlm_consent.isChecked()
        cfg.save(self.paths.settings_path)
        self.saved.setText(f"Saved to {self.paths.settings_path}")
