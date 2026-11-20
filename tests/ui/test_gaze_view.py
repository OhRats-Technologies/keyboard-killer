"""UI tests for the gaze field and speech test panel."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from keyboard_killer.audio.quick_test import SpeechTestResult
from keyboard_killer.config.paths import HOME_ENV, default_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.ui.gaze_source import GazeSample
from keyboard_killer.ui.screen_cursor import CursorStatus
from keyboard_killer.ui.theme.tokens import ThemeName


def _window(tmp_path):
    from keyboard_killer.ui.app import MainWindow

    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    return MainWindow(
        paths=paths,
        config=AppConfig(),
        theme=ThemeName.LIGHT,
        enable_previews=False,
    )


def test_main_window_gaze_field_switches_between_demo_and_live(qapp, tmp_path) -> None:
    window = _window(tmp_path)

    class Source:
        def __init__(self, sample):
            self.sample = sample

        def read(self):
            return self.sample

        def close(self):
            pass

    try:
        field = window.home.gaze_field
        field._gaze = Source(
            GazeSample(x=120.0, y=80.0, face=True, blinking=False, age_s=0.02, seq=1)
        )
        field._tick()
        assert field.mode == "live"

        field._gaze = Source(
            GazeSample(x=120.0, y=80.0, face=True, blinking=False, age_s=2.0, seq=2)
        )
        field._tick()
        assert field.mode == "demo"
    finally:
        window.shutdown()
        window.close()


def test_home_screen_cursor_toggle_uses_the_overlay_controller(qapp, tmp_path) -> None:
    window = _window(tmp_path)

    class FakeCursor:
        def __init__(self):
            self.running = False
            self.error = ""

        def start(self):
            self.running = True
            return CursorStatus(True, "running")

        def stop(self):
            self.running = False
            return CursorStatus(False)

        def refresh(self):
            return CursorStatus(self.running)

    class FailingCursor(FakeCursor):
        def start(self):
            self.error = "No X11 display."
            return CursorStatus(False, "", self.error)

    try:
        fake = FakeCursor()
        window.screen_cursor = fake
        window.home.cursor_button.click()
        assert fake.running
        assert window.home.cursor_button.text() == "HIDE SCREEN CURSOR"
        window.home.cursor_button.click()
        assert not fake.running
        assert window.home.cursor_button.text() == "SHOW SCREEN CURSOR"

        failing = FailingCursor()
        window.screen_cursor = failing
        window.home.cursor_button.click()
        assert not window.home.cursor_button.isChecked()
        assert "No X11 display" in window.home.cursor_note.text()
    finally:
        window.shutdown()
        window.close()


def test_models_panel_renders_a_speech_test_result(qapp, tmp_path) -> None:
    window = _window(tmp_path)
    panel = window.pages["Models"]
    try:
        panel._on_done(
            SpeechTestResult(
                transcript="click send",
                latency_ms=42.0,
                recorded_s=4.0,
                sample_rate=16000,
                peak=0.22,
                engine="parakeet-tdt-0.6b-v2",
            )
        )
        assert "click send" in panel.test_transcript.text()
        assert "42 MS" in panel.test_detail.text()
        assert panel.test_status.text() == "HEARD · LOCAL PARAKEET"
        assert panel.test_button.isEnabled()
    finally:
        window.shutdown()
        window.close()
