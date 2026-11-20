"""UI tests for the gaze field, quick controls, and speech/camera settings."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from keyless.audio.quick_test import SpeechTestResult
from keyless.config.paths import HOME_ENV, default_paths
from keyless.config.schema import AppConfig
from keyless.ui.gaze_source import GazeSample
from keyless.ui.screen_cursor import CursorStatus
from keyless.ui.theme.tokens import ThemeName


def _window(tmp_path):
    from keyless.ui.app import MainWindow

    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    return MainWindow(
        paths=paths,
        config=AppConfig(),
        theme=ThemeName.LIGHT,
        enable_previews=False,
    )


def test_main_window_gaze_field_switches_between_idle_and_live(qapp, tmp_path) -> None:
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
        assert field.mode == "idle"
    finally:
        window.shutdown()
        window.close()


def test_home_screen_cursor_toggle_uses_the_overlay_controller(qapp, tmp_path) -> None:
    window = _window(tmp_path)

    class FakeCursor:
        def __init__(self):
            self.running = False
            self.error = ""

        def start(self, live_region=None):
            self.running = True
            return CursorStatus(True, "running")

        def stop(self):
            self.running = False
            return CursorStatus(False)

        def refresh(self):
            return CursorStatus(self.running)

    class FailingCursor(FakeCursor):
        def start(self, live_region=None):
            self.error = "No X11 display."
            return CursorStatus(False, "", self.error)

    try:
        fake = FakeCursor()
        window.screen_cursor = fake
        window.home.cursor_button.click()
        assert fake.running
        assert window.home.cursor_button.text() == "Hide gaze dot"
        window.home.cursor_button.click()
        assert not fake.running
        assert window.home.cursor_button.text() == "Show gaze dot"

        failing = FailingCursor()
        window.screen_cursor = failing
        window.home.cursor_button.click()
        assert not window.home.cursor_button.isChecked()
        assert "No X11 display" in window.home.cursor_note.text()
    finally:
        window.shutdown()
        window.close()


def test_settings_panel_renders_speech_test_and_toggles_pipewire_sharing(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("keyless.ui.settings.window.pipewire_available", lambda: True)
    window = _window(tmp_path)
    window.config.camera_mode = "mono"
    window.config.cam1_by_id = "/dev/v4l/by-id/cam-a"
    panel = window.settings
    try:
        panel._on_speech_done(
            SpeechTestResult(
                transcript="click send",
                latency_ms=42.0,
                recorded_s=4.0,
                sample_rate=16000,
                peak=0.22,
                engine="parakeet-tdt-0.6b-v2",
            )
        )
        assert "click send" in panel.mic_result.text()
        assert "42 ms" in panel.mic_result.text()

        panel.refresh()
        assert panel.share_camera.isEnabled()
        panel.share_camera.setChecked(True)
        assert AppConfig.load(window.paths.settings_path).camera_backend == "pipewire"
    finally:
        window.shutdown()
        window.close()


def test_home_transcript_overlay_toggle_uses_the_controller(qapp, tmp_path) -> None:
    window = _window(tmp_path)

    class FakeTranscript:
        def __init__(self):
            self.running = False
            self.error = ""

        def start(self):
            self.running = True
            return True

        def stop(self):
            self.running = False

        def refresh(self):
            return self.running

    try:
        fake = FakeTranscript()
        window.transcript_overlay = fake
        window.home.transcript_button.click()
        assert fake.running
        assert window.home.transcript_button.text() == "Hide live captions"
        assert window.config.transcript_overlay is True
        window.home.transcript_button.click()
        assert not fake.running
        assert window.home.transcript_button.text() == "Show live captions"
        assert window.config.transcript_overlay is False
    finally:
        window.shutdown()
        window.close()
