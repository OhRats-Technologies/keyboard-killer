"""Onboarding pages and window (welcome -> ... -> ready)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.diagnostics.doctor import DoctorReport, collect_doctor
from keyboard_killer.models.catalog import get_asset
from keyboard_killer.models.downloader import DownloadProgress, download_asset
from keyboard_killer.models.providers.acp_registry import fetch_registry
from keyboard_killer.models.providers.openai_compat import OpenAICompatProvider
from keyboard_killer.models.providers.presets import load_presets
from keyboard_killer.models.providers.secrets import keyring_available, set_secret
from keyboard_killer.platform.linux.cameras import discover_v4l_by_id
from keyboard_killer.ui.onboarding.flow import (
    OPTIONAL_STEPS,
    OnboardingFlow,
    OnboardingStep,
    STEPS,
)
from keyboard_killer.voice.stt.factory import stt_report


class CameraPreview(QLabel):
    """Small live preview; disabled on headless test runs."""

    def __init__(self, name: str = "") -> None:
        super().__init__("No preview")
        self.name = name
        self.setMinimumSize(260, 150)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #101216; color: #9aa3b2; border-radius: 8px;")
        self._cap = None
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._read)

    def start(self, path: str) -> None:
        self.stop()
        if not path:
            self.setText("Choose a camera")
            return
        try:
            import cv2

            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                self.setText(f"Could not open\n{Path(path).name}")
                return
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 480)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 270)
            self._cap = cap
            self.setText("")
            self._timer.start()
        except Exception:
            self.setText("Preview unavailable")

    def _read(self) -> None:
        if self._cap is None:
            return
        import cv2

        ok, frame = self._cap.read()
        if not ok or frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()
        self.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def stop(self) -> None:
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class StepPage(QWidget):
    step: OnboardingStep
    next_label = "Continue"
    skippable = False

    def __init__(self) -> None:
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(12)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        self.layout.addWidget(self.title)
        self.layout.addWidget(self.subtitle)
        self.body = QVBoxLayout()
        self.layout.addLayout(self.body, 1)
        self.problem = QLabel("")
        self.problem.setObjectName("Warning")
        self.problem.setWordWrap(True)
        self.layout.addWidget(self.problem)

    def on_enter(self) -> None:
        return None

    def validate(self) -> tuple[bool, str]:
        return True, ""

    @property
    def unresolved(self) -> bool:
        return False


class WelcomePage(StepPage):
    step = OnboardingStep.WELCOME
    next_label = "Get started"

    def __init__(self) -> None:
        super().__init__()
        self.title.setText("Welcome to Keyboard-Killer")
        self.subtitle.setText(
            "Look at a target and say what to do. Voice and camera processing stays on this device."
        )
        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        for text in (
            "1. Check camera and input permissions.",
            "2. Confirm both cameras and run calibration.",
            "3. Download the English speech model.",
            "4. Practice once, then start listening.",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            card_layout.addWidget(label)
        self.body.addWidget(card)
        self.body.addStretch(1)


class PermissionsPage(StepPage):
    step = OnboardingStep.PERMISSIONS
    next_label = "Continue"

    def __init__(self, report_provider: Callable[[], DoctorReport] | None = None) -> None:
        super().__init__()
        self.title.setText("Permissions")
        self.subtitle.setText(
            "Each row has one action. A denied permission marks the step unresolved, but it does not block setup."
        )
        self.report_provider = report_provider
        self.rows = QGridLayout()
        self.body.addLayout(self.rows)
        self.body.addStretch(1)
        refresh = QPushButton("Check again")
        refresh.clicked.connect(self.refresh)
        self.body.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self._report: DoctorReport | None = None

    def on_enter(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        try:
            report = (
                self.report_provider()
                if self.report_provider is not None
                else collect_doctor(check_providers=False)
            )
        except Exception as exc:
            self.problem.setText(f"Could not read permissions: {exc}")
            return
        self._report = report
        permissions = report.permissions
        for row, (name, info) in enumerate(permissions.items()):
            label = QLabel(name.replace("_", " ").capitalize())
            state = QLabel(info.get("state", "unknown"))
            state.setObjectName("Success" if info.get("state") == "granted" else "Warning")
            action = QLabel(info.get("action") or info.get("detail", ""))
            action.setObjectName("Muted")
            action.setWordWrap(True)
            self.rows.addWidget(label, row, 0)
            self.rows.addWidget(state, row, 1)
            self.rows.addWidget(action, row, 2)
        self.problem.setText("")

    @property
    def unresolved(self) -> bool:
        if self._report is None:
            return True
        return any(
            info.get("state") in ("denied", "needs_restart")
            for info in self._report.permissions.values()
        )


class CamerasPage(StepPage):
    step = OnboardingStep.CAMERAS
    next_label = "Continue"

    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        *,
        enable_previews: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.paths = paths
        self.enable_previews = enable_previews
        self.title.setText("Cameras")
        self.subtitle.setText(
            "Stereo gaze uses two cameras. Pick a stable device for each slot; previews show which is which."
        )
        grid = QGridLayout()
        self.cam1 = QComboBox()
        self.cam2 = QComboBox()
        self.preview1 = CameraPreview("cam1")
        self.preview2 = CameraPreview("cam2")
        grid.addWidget(QLabel("Camera 1"), 0, 0)
        grid.addWidget(self.cam1, 0, 1)
        grid.addWidget(QLabel("Camera 2"), 1, 0)
        grid.addWidget(self.cam2, 1, 1)
        grid.addWidget(self.preview1, 2, 0)
        grid.addWidget(self.preview2, 2, 1)
        self.body.addLayout(grid)
        buttons = QHBoxLayout()
        swap = QPushButton("Swap cameras")
        swap.clicked.connect(self.swap)
        buttons.addWidget(swap)
        buttons.addStretch(1)
        self.body.addLayout(buttons)
        self.body.addStretch(1)
        self.refresh_devices()

    def refresh_devices(self) -> None:
        devices = discover_v4l_by_id()
        for combo, current in ((self.cam1, self.config.cam1_by_id), (self.cam2, self.config.cam2_by_id)):
            combo.clear()
            combo.addItem("Choose a camera", "")
            for device in devices:
                combo.addItem(Path(device).name, device)
            index = combo.findData(current)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.currentIndexChanged.connect(self._start_previews)
        self._start_previews()
        if len(devices) < 2:
            self.problem.setText(
                "Fewer than two stable cameras found. Stereo gaze needs two; you can continue and finish hardware setup later."
            )
        else:
            self.problem.setText("")

    def _start_previews(self) -> None:
        self.config.cam1_by_id = str(self.cam1.currentData() or "")
        self.config.cam2_by_id = str(self.cam2.currentData() or "")
        if not self.enable_previews:
            self.preview1.setText("Preview disabled in tests")
            self.preview2.setText("Preview disabled in tests")
            return
        self.preview1.start(self.config.cam1_by_id)
        self.preview2.start(self.config.cam2_by_id)

    def swap(self) -> None:
        first = self.cam1.currentIndex()
        self.cam1.setCurrentIndex(self.cam2.currentIndex())
        self.cam2.setCurrentIndex(first)
        self._start_previews()

    def on_enter(self) -> None:
        self.refresh_devices()

    def validate(self) -> tuple[bool, str]:
        if (
            self.config.cam1_by_id
            and self.config.cam2_by_id
            and self.config.cam1_by_id == self.config.cam2_by_id
        ):
            return False, "Camera 1 and camera 2 must be different devices."
        return True, ""

    @property
    def unresolved(self) -> bool:
        return not (self.config.cam1_by_id and self.config.cam2_by_id)

    def close_previews(self) -> None:
        self.preview1.stop()
        self.preview2.stop()


class CalibrationPage(StepPage):
    step = OnboardingStep.CALIBRATION
    next_label = "Continue"

    def __init__(self, paths: AppPaths, runner: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.paths = paths
        self.runner = runner
        self.title.setText("Calibration")
        self.subtitle.setText(
            "Calibration teaches the gaze model about your screen and posture. It opens a full-screen target window."
        )
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.body.addWidget(self.status)
        actions = QHBoxLayout()
        self.run_button = QPushButton("Run calibration")
        self.run_button.setObjectName("Primary")
        self.run_button.clicked.connect(self.run_calibration)
        actions.addWidget(self.run_button)
        recheck = QPushButton("Check again")
        recheck.clicked.connect(self.refresh)
        actions.addWidget(recheck)
        actions.addStretch(1)
        self.body.addLayout(actions)
        self.body.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        exists = self.paths.calibration_path.exists()
        self.status.setText(
            f"Calibration file: {self.paths.calibration_path} ({'ready' if exists else 'missing'})"
        )
        self.status.setObjectName("Success" if exists else "Warning")

    def run_calibration(self) -> None:
        if self.runner is not None:
            self.runner()
            return
        try:
            subprocess.Popen([sys.executable, "-m", "keyboard_killer.main", "calibrate"])
            self.problem.setText(
                "Calibration opened in a separate window. Finish it here, then choose Check again."
            )
        except OSError as exc:
            self.problem.setText(f"Could not start calibration: {exc}")

    def on_enter(self) -> None:
        self.refresh()

    def validate(self) -> tuple[bool, str]:
        if not self.paths.calibration_path.exists():
            return False, "Run calibration before continuing."
        return True, ""


class _DownloadWorker(QThread):
    progress = Signal(object)
    done = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            asset = get_asset("parakeet-tdt-0.6b-v2")
            receipt = download_asset(asset, progress=lambda p: self.progress.emit(p))
            self.done.emit(receipt)
        except Exception as exc:
            self.failed.emit(str(exc))


class SttPage(StepPage):
    step = OnboardingStep.STT
    next_label = "Continue"

    def __init__(self, config: AppConfig, paths: AppPaths) -> None:
        super().__init__()
        self.config = config
        self.paths = paths
        self.title.setText("Speech")
        self.subtitle.setText(
            "Parakeet is the app-owned English model. It runs on the CPU; the Whisper fallback stays available."
        )
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.body.addWidget(self.progress)
        actions = QHBoxLayout()
        self.download_button = QPushButton("Download model")
        self.download_button.setObjectName("Primary")
        self.download_button.clicked.connect(self.download)
        actions.addWidget(self.download_button)
        self.whisper_button = QPushButton("Use Whisper fallback")
        self.whisper_button.clicked.connect(self.use_whisper)
        actions.addWidget(self.whisper_button)
        actions.addStretch(1)
        self.body.addLayout(actions)
        self.body.addStretch(1)
        self._worker: _DownloadWorker | None = None
        self.refresh()

    def refresh(self) -> None:
        report = stt_report(self.config, paths=self.paths)
        parakeet = report.get("parakeet", {})
        state = parakeet.get("state", "unknown") if isinstance(parakeet, dict) else "unknown"
        self.status.setText(
            f"Selected: {report.get('selected')} | Parakeet: {state} | "
            f"Whisper fallback: {'installed' if report.get('whisper', {}).get('available') else 'missing'}"
        )
        self.status.setObjectName("Success" if state == "ready" else "Warning")
        self.download_button.setEnabled(state != "ready")
        self.download_button.setText("Model ready" if state == "ready" else "Download model (630 MB)")

    def download(self) -> None:
        if self._worker is not None:
            return
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.download_button.setEnabled(False)
        worker = _DownloadWorker(self)
        worker.progress.connect(self._on_progress)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_progress(self, update: DownloadProgress) -> None:
        self.progress.setValue(int(update.percent))

    def _on_done(self, _receipt: object) -> None:
        self._worker = None
        self.refresh()
        self.progress.setVisible(False)
        self.problem.setText("")

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.download_button.setEnabled(True)
        self.progress.setVisible(False)
        self.problem.setText(f"Download failed: {message}")

    def use_whisper(self) -> None:
        self.config.stt_engine = "whisper"
        self.config.save(self.paths.settings_path)
        self.refresh()

    def validate(self) -> tuple[bool, str]:
        report = stt_report(self.config, paths=self.paths)
        if report.get("selected") == "unavailable":
            return False, "Download Parakeet or install the Whisper fallback."
        return True, ""

    def close_worker(self) -> None:
        if self._worker is not None:
            self._worker.wait(1000)


class ProviderPage(StepPage):
    step = OnboardingStep.PROVIDER
    next_label = "Continue"
    skippable = True

    def __init__(self, config: AppConfig, paths: AppPaths) -> None:
        super().__init__()
        self.config = config
        self.paths = paths
        self.title.setText("Connect an AI (optional)")
        self.subtitle.setText(
            "Pointer and voice commands work without an AI. A provider adds free-form terminal/browser planning."
        )
        form = QFormLayout()
        self.preset = QComboBox()
        for item in load_presets():
            self.preset.addItem(item.name, item.id)
        self.model = QLineEdit(self.config.llm_model)
        self.model.setPlaceholderText("Provider default")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Stored in the OS keychain")
        form.addRow("Endpoint", self.preset)
        form.addRow("Model", self.model)
        form.addRow("API key", self.key)
        self.body.addLayout(form)
        actions = QHBoxLayout()
        save = QPushButton("Save key")
        save.clicked.connect(self.save_key)
        actions.addWidget(save)
        test = QPushButton("Test connection")
        test.clicked.connect(self.test)
        actions.addWidget(test)
        self.acp = QPushButton("Use an ACP agent")
        self.acp.clicked.connect(self.acp_status)
        actions.addWidget(self.acp)
        actions.addStretch(1)
        self.body.addLayout(actions)
        self.result = QLabel("Optional: skip for now.")
        self.result.setObjectName("Muted")
        self.result.setWordWrap(True)
        self.body.addWidget(self.result)
        self.body.addStretch(1)

    def save_key(self) -> None:
        if not keyring_available():
            self.result.setText("OS keychain unavailable; install the keyring backend.")
            return
        key = self.key.text().strip()
        if not key:
            self.result.setText("Enter a key first.")
            return
        preset = str(self.preset.currentData())
        set_secret(preset, key)
        self.config.llm_provider = "openai_compat"
        self.config.llm_preset = preset
        self.config.llm_model = self.model.text().strip()
        self.config.save(self.paths.settings_path)
        self.key.clear()
        self.result.setText(f"Stored {preset} key in the OS keychain and saved the model.")

    def test(self) -> None:
        preset = str(self.preset.currentData())
        provider = OpenAICompatProvider(preset, self.model.text().strip())
        status = provider.status()
        self.result.setText(f"{provider.display_name}: {status.state.value} — {status.detail}")
        self.result.setObjectName("Success" if status.ready else "Warning")

    def acp_status(self) -> None:
        try:
            installed = [spec for spec in fetch_registry() if spec.installed]
        except Exception as exc:
            self.result.setText(f"Could not read the ACP registry: {exc}")
            return
        names = ", ".join(spec.name for spec in installed[:6]) or "none detected"
        self.result.setText(f"Installed ACP agents: {names}. Choose one in Settings.")

    def validate(self) -> tuple[bool, str]:
        return True, ""


class PracticePage(StepPage):
    step = OnboardingStep.PRACTICE
    next_label = "Continue"

    def __init__(self) -> None:
        super().__init__()
        self.title.setText("Practice")
        self.subtitle.setText(
            "Look at the target, say \u201cclick\u201d. This card simulates the click so the first live action is not a surprise."
        )
        self.target = QPushButton("Click me")
        self.target.setObjectName("Primary")
        self.target.setMinimumSize(240, 140)
        self.target.clicked.connect(self.practice)
        self.body.addWidget(self.target, 0, Qt.AlignmentFlag.AlignCenter)
        self.result = QLabel("Waiting for a practice click.")
        self.result.setObjectName("Muted")
        self.result.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(self.result)
        self.body.addStretch(1)
        self._done = False

    def practice(self) -> None:
        self._done = True
        self.result.setText("Practice click landed. You are ready to try the desktop.")
        self.result.setObjectName("Success")

    def validate(self) -> tuple[bool, str]:
        if not self._done:
            return False, "Try the practice click once."
        return True, ""


class ReadyPage(StepPage):
    step = OnboardingStep.READY
    next_label = "Finish"

    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.title.setText("You're ready")
        self.subtitle.setText(
            "Keyboard-Killer lives in the tray. Pause any time; quit from the tray menu."
        )
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.addWidget(QLabel("Listening state is shown in the tray and the status pill."))
        layout.addWidget(QLabel(f"Settings: {self.paths.settings_path}"))
        layout.addWidget(QLabel(f"History: {self.paths.history_db_path}"))
        self.body.addWidget(card)
        self.body.addStretch(1)


class OnboardingWindow(QDialog):
    def __init__(
        self,
        *,
        flow: OnboardingFlow | None = None,
        config: AppConfig | None = None,
        paths: AppPaths | None = None,
        report_provider: Callable[[], DoctorReport] | None = None,
        enable_previews: bool = True,
        calibration_runner: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths or current_paths()
        self.config = config or AppConfig.load(self.paths.settings_path)
        self.flow = flow or OnboardingFlow(self.paths)
        self.setWindowTitle("Set up Keyboard-Killer")
        self.resize(940, 640)

        self.stack = QStackedWidget()
        self.pages: dict[OnboardingStep, StepPage] = {
            OnboardingStep.WELCOME: WelcomePage(),
            OnboardingStep.PERMISSIONS: PermissionsPage(report_provider),
            OnboardingStep.CAMERAS: CamerasPage(
                self.config, self.paths, enable_previews=enable_previews
            ),
            OnboardingStep.CALIBRATION: CalibrationPage(self.paths, calibration_runner),
            OnboardingStep.STT: SttPage(self.config, self.paths),
            OnboardingStep.PROVIDER: ProviderPage(self.config, self.paths),
            OnboardingStep.PRACTICE: PracticePage(),
            OnboardingStep.READY: ReadyPage(self.paths),
        }
        for step in STEPS:
            self.stack.addWidget(self.pages[step])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        self.step_label = QLabel("")
        self.step_label.setObjectName("Muted")
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)

        footer = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back)
        footer.addWidget(self.back_button)
        self.skip_button = QPushButton("Skip for now")
        self.skip_button.clicked.connect(self.skip)
        footer.addWidget(self.skip_button)
        footer.addStretch(1)
        self.next_button = QPushButton("Continue")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self.next)
        footer.addWidget(self.next_button)
        layout.addLayout(footer)

        self._show(self.flow.step)

    def _show(self, step: OnboardingStep) -> None:
        page = self.pages[step]
        self.stack.setCurrentWidget(page)
        self.step_label.setText(f"Step {STEPS.index(step) + 1} of {len(STEPS)} — {step.value.capitalize()}")
        self.back_button.setEnabled(step is not OnboardingStep.WELCOME)
        self.next_button.setText(page.next_label)
        self.skip_button.setVisible(step in OPTIONAL_STEPS)
        page.on_enter()

    def next(self) -> None:
        page = self.pages[self.flow.step]
        ok, reason = page.validate()
        if not ok:
            page.problem.setText(reason)
            return
        page.problem.setText("")
        if self.flow.step is OnboardingStep.READY:
            self.flow.advance()
            self.accept()
            return
        self.flow.advance(unresolved=page.unresolved)
        self._show(self.flow.step)

    def back(self) -> None:
        self.flow.back()
        self._show(self.flow.step)

    def skip(self) -> None:
        if self.flow.step not in OPTIONAL_STEPS:
            return
        self.flow.advance(skipped=True)
        self._show(self.flow.step)

    def closeEvent(self, event) -> None:  # noqa: N802
        for page in self.pages.values():
            if isinstance(page, CamerasPage):
                page.close_previews()
            if isinstance(page, SttPage):
                page.close_worker()
        super().closeEvent(event)
