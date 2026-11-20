"""Headless UI tests for onboarding, settings, history, overlay, and goldens."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from keyboard_killer.config.paths import HOME_ENV, default_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.ui.history.db import ActionRecord, HistoryStore
from keyboard_killer.ui.history.window import HistoryPanel
from keyboard_killer.ui.onboarding.flow import OnboardingFlow, OnboardingStep
from keyboard_killer.ui.onboarding.steps import OnboardingWindow
from keyboard_killer.ui.overlay.base import OverlayMode, OverlayState, OverlayUpdate
from keyboard_killer.ui.overlay.linux import TrayToastOverlay, overlay_mode
from keyboard_killer.ui.session import NullController
from keyboard_killer.ui.settings.window import SettingsPanel
from keyboard_killer.ui.theme.tokens import ThemeName

GOLDENS = Path(__file__).with_name("goldens")


def _paths(tmp_path: Path):
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    return paths


def _report():
    return SimpleNamespace(
        permissions={
            "input_injection": {
                "state": "granted",
                "detail": "uinput writable",
                "action": "",
            },
            "microphone": {"state": "granted", "detail": "mic found", "action": ""},
        }
    )


def _compare_golden(pixmap, name: str, tolerance: float = 6.0) -> None:
    # Text antialiasing differs slightly between Linux font stacks/Qt patch levels.
    # Six percent still catches layout and token regressions.
    golden_path = GOLDENS / name
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.width() > 400 and image.height() > 300
    if not golden_path.exists():
        pixmap.save(str(golden_path))
        pytest.fail(f"Golden did not exist; wrote {golden_path}. Re-run to compare.")
    golden = QImage(str(golden_path)).convertToFormat(QImage.Format.Format_RGBA8888)
    assert (golden.width(), golden.height()) == (image.width(), image.height())
    total = 0
    different = 0
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            left = image.pixelColor(x, y)
            right = golden.pixelColor(x, y)
            delta = (
                abs(left.red() - right.red())
                + abs(left.green() - right.green())
                + abs(left.blue() - right.blue())
            ) / 3
            total += 1
            if delta > 12:
                different += 1
    ratio = different / max(1, total)
    assert ratio < tolerance / 100, f"{name}: {ratio:.2%} pixels differ"


def test_onboarding_flow_persists_and_resumes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    flow = OnboardingFlow(paths)
    assert flow.step is OnboardingStep.WELCOME
    flow.advance()
    flow.advance(unresolved=True)
    assert flow.step is OnboardingStep.CAMERAS
    assert "welcome" in flow.state.completed
    assert "permissions" in flow.state.unresolved

    resumed = OnboardingFlow(paths)
    assert resumed.step is OnboardingStep.CAMERAS
    assert "permissions" in resumed.state.unresolved
    resumed.back()
    assert resumed.step is OnboardingStep.PERMISSIONS
    resumed.reset()
    assert resumed.step is OnboardingStep.WELCOME
    assert json.loads(paths.onboarding_path.read_text())["version"] == 1


def test_onboarding_window_resumes_and_reaches_ready(qapp, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    calibration = paths.calibration_path
    calibration.write_text('{"rmse_x": 12.0}\n')
    flow = OnboardingFlow(paths)
    flow.go_to(OnboardingStep.WELCOME)
    window = OnboardingWindow(
        flow=flow,
        config=AppConfig(cam1_by_id="/dev/video0", cam2_by_id="/dev/video1"),
        paths=paths,
        report_provider=_report,
        enable_previews=False,
    )
    try:
        assert window.stack.currentIndex() == 0
        window.next()  # welcome -> permissions
        assert flow.step is OnboardingStep.PERMISSIONS
        window.next()  # permissions -> cameras
        assert flow.step is OnboardingStep.CAMERAS
        window.next()  # cameras -> calibration
        assert flow.step is OnboardingStep.CALIBRATION
        window.next()  # calibration exists -> stt
        window.pages[OnboardingStep.PRACTICE].practice()
        window.next()
        window.pages[OnboardingStep.PROVIDER].validate()
        window.skip()
        window.next()  # practice -> ready
        assert flow.step is OnboardingStep.READY
        window.next()  # finish
        assert flow.is_complete
        assert json.loads(paths.onboarding_path.read_text())["step"] == "ready"
    finally:
        window.close()


def test_settings_panel_saves_same_config(qapp, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    config = AppConfig()
    panel = SettingsPanel(config, paths)
    panel.hotkey.setText("KEY_F9")
    panel.always_on.setChecked(False)
    panel.stt_engine.setCurrentText("whisper")
    panel.save()
    loaded = AppConfig.load(paths.settings_path)
    assert loaded.ptt_hotkey == "KEY_F9"
    assert loaded.always_on_voice is False
    assert loaded.stt_engine == "whisper"
    assert loaded.llm_provider == "openai_compat"


def test_history_store_and_panel(qapp, tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    store = HistoryStore(paths)
    store.add(ActionRecord(transcript="click send", route="gaze", target="Send", result="clicked", latency_ms=42))
    rows = store.recent()
    assert rows[0]["transcript"] == "click send"
    panel = HistoryPanel(store)
    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 1).text() == "click send"
    panel.clear()
    assert panel.table.rowCount() == 0


def test_overlay_fallback_is_honest_on_gnome_wayland() -> None:
    gnome = overlay_mode({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"})
    assert gnome.mode is OverlayMode.TRAY_TOAST
    assert gnome.available
    assert "GNOME Wayland" in gnome.reason
    assert not gnome.persistent_indicator

    kde = overlay_mode({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"})
    assert kde.mode is OverlayMode.TRAY_TOAST
    assert "portable click-through" in kde.reason

    x11 = overlay_mode({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "GNOME"})
    assert x11.mode is OverlayMode.TRANSPARENT_WINDOW
    overlay = TrayToastOverlay(tray=None)
    overlay.show_state(OverlayUpdate(OverlayState.LISTENING, "Listening"))
    assert overlay.capability().mode is OverlayMode.TRAY_TOAST


def test_main_window_navigation_and_theme(qapp, tmp_path: Path) -> None:
    from keyboard_killer.ui.app import MainWindow

    paths = _paths(tmp_path)
    window = MainWindow(
        paths=paths,
        config=AppConfig(),
        controller=NullController(),
        theme=ThemeName.LIGHT,
        enable_previews=False,
    )
    try:
        assert window.stack.count() == 8
        window.show_page("History")
        assert window.stack.currentWidget() is window.pages["History"]
        window.set_theme(ThemeName.DARK)
        assert window.theme_tokens.name is ThemeName.DARK
        window.home.toggle()
        assert window.controller.status.state.value == "listening"
        window.home.toggle()
        assert window.controller.status.state.value == "paused"
    finally:
        window.close()


def test_main_window_light_and_dark_goldens(qapp, tmp_path: Path) -> None:
    from keyboard_killer.ui.app import MainWindow

    paths = _paths(tmp_path)
    for theme in (ThemeName.LIGHT, ThemeName.DARK):
        window = MainWindow(
            paths=paths,
            config=AppConfig(),
            controller=NullController(),
            theme=theme,
            enable_previews=False,
        )
        try:
            window.resize(1120, 720)
            window.show()
            qapp.processEvents()
            _compare_golden(window.grab(), f"main-{theme.value}.png")
        finally:
            window.close()


def test_onboarding_welcome_light_and_dark_goldens(qapp, tmp_path: Path) -> None:
    from keyboard_killer.ui.onboarding.steps import OnboardingWindow
    from keyboard_killer.ui.theme.tokens import stylesheet, tokens_for

    paths = _paths(tmp_path)
    flow = OnboardingFlow(paths)
    for theme in (ThemeName.LIGHT, ThemeName.DARK):
        qapp.setStyleSheet(stylesheet(tokens_for(theme)))
        window = OnboardingWindow(
            flow=flow,
            config=AppConfig(),
            paths=paths,
            report_provider=_report,
            enable_previews=False,
        )
        try:
            window.resize(940, 640)
            window.show()
            qapp.processEvents()
            _compare_golden(window.grab(), f"onboarding-{theme.value}.png")
        finally:
            window.close()
