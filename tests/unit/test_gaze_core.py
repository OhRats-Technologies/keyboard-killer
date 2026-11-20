"""Headless tests for the gaze view, demo path, and speech smoke test."""

from __future__ import annotations

import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from keyless.audio.quick_test import DEFAULT_SECONDS, run_speech_test
from keyless.config.schema import AppConfig
from keyless.ipc.state import FLAG_BLINKING, FLAG_FACE_DETECTED, GazeState
from keyless.ui.gaze_art import demo_gaze
from keyless.ui.gaze_source import gaze_state_sample
from keyless.ui.screen_cursor import ScreenCursor
from keyless.voice.stt.base import SttResult


def test_demo_gaze_stays_on_screen_and_moves() -> None:
    points = [demo_gaze(t * 0.37) for t in range(400)]
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in points)
    xs = {round(x, 3) for x, _ in points}
    ys = {round(y, 3) for _, y in points}
    assert len(xs) > 20 and len(ys) > 20


def test_gaze_state_sample_marks_face_and_blink() -> None:
    now = time.monotonic_ns()
    state = GazeState(
        x=400.0,
        y=300.0,
        flags=FLAG_FACE_DETECTED | FLAG_BLINKING,
        timestamp_ns=now - 40_000_000,
        seq=7,
    )
    sample = gaze_state_sample(state, now_ns=now)
    assert sample is not None
    assert sample.face and sample.blinking
    assert sample.age_s == pytest.approx(0.04, abs=0.01)
    assert sample.live


def test_gaze_state_sample_rejects_missing_timestamp() -> None:
    assert gaze_state_sample(GazeState(0.0, 0.0, 0, 0, 0)) is None


def test_screen_cursor_availability_is_honest(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert ScreenCursor.availability({"DISPLAY": ":0"})[0]
    ok, reason = ScreenCursor.availability({"DISPLAY": ""})
    assert not ok and "X11" in reason
    monkeypatch.setattr("sys.platform", "darwin")
    ok, reason = ScreenCursor.availability({"DISPLAY": ":0"})
    assert not ok and "Linux" in reason


class FakeEngine:
    name = "fake-parakeet"

    def transcribe_detailed(self, audio: np.ndarray) -> SttResult:
        assert audio.shape == (int(DEFAULT_SECONDS * 16000),)
        return SttResult(text=" click send ", latency_ms=18.5)


def test_run_speech_test_uses_the_local_engine(monkeypatch) -> None:
    def fake_record(seconds, *, sample_rate=16000, device=None):
        assert seconds == DEFAULT_SECONDS
        return np.full(int(seconds * sample_rate), 0.2, dtype=np.float32)

    monkeypatch.setattr("keyless.audio.quick_test.record_clip", fake_record)
    stages: list[str] = []
    result = run_speech_test(AppConfig(), engine=FakeEngine(), on_stage=stages.append)
    assert result.transcript == "click send"
    assert result.latency_ms == 18.5
    assert result.recorded_s == pytest.approx(DEFAULT_SECONDS)
    assert result.peak == pytest.approx(0.2)
    assert result.has_speech
    assert stages == ["recording", "transcribing"]


def _layout():
    from keyless.platform.base import DisplayLayout, MonitorInfo

    return DisplayLayout(
        monitors=(
            MonitorInfo("DP-2", 0, 0, 1920, 1080, 1920, 1080, 1.0, True),
            MonitorInfo("HDMI-A-1", 1920, 0, 1920, 1080, 1920, 1080, 1.0, False),
        ),
        total_width=3840,
        total_height=1080,
    )


def test_calibration_region_places_bounds_on_their_monitor(tmp_path) -> None:
    import json

    from keyless.ui.gaze_region import calibration_region

    path = tmp_path / "calib.json"
    path.write_text(
        json.dumps(
            {
                "monitor_connector": "HDMI-A-1",
                "bounds": {"min_x": 0.0, "min_y": 0.0, "max_x": 1919.0, "max_y": 1079.0},
            }
        )
    )
    region = calibration_region(path, _layout())
    assert (region.x, region.y) == (1920, 0)
    assert (region.width, region.height) == (1920, 1080)
    assert region.source_width == 1920.0


def test_calibration_region_falls_back_to_primary(tmp_path) -> None:
    import json

    from keyless.ui.gaze_region import calibration_region

    path = tmp_path / "calib.json"
    path.write_text(json.dumps({"monitor_connector": "MISSING", "bounds": {}}))
    region = calibration_region(path, _layout())
    assert (region.x, region.y) == (0, 0)
    assert (region.width, region.height) == (1920, 1080)


def test_cursor_motion_converges_without_overshoot() -> None:
    from keyless.ui.cursor_motion import GazeMotion

    motion = GazeMotion()
    motion.reset(0.0, 0.0)
    peak = 0.0
    for index in range(180):
        motion.update(200.0, 100.0, 1.0 / 120.0)
        peak = max(peak, motion.x)
    assert motion.x == pytest.approx(200.0, abs=1.0)
    assert motion.y == pytest.approx(100.0, abs=1.0)
    assert peak < 202.0


def test_cursor_motion_holds_a_fixation_against_jitter() -> None:
    import math

    from keyless.ui.cursor_motion import GazeMotion, MotionSettings

    motion = GazeMotion(
        MotionSettings(fixation_radius=22.0, fixation_break_radius=40.0, search_spread=80.0)
    )
    motion.reset(100.0, 100.0)
    for index in range(240):
        motion.update(
            100.0 + 7.0 * math.sin(index * 0.9),
            100.0 + 7.0 * math.cos(index * 1.1),
            1.0 / 120.0,
        )
    assert motion.x == pytest.approx(100.0, abs=3.0)
    assert motion.y == pytest.approx(100.0, abs=3.0)


def test_cursor_motion_holds_when_no_face_is_present() -> None:
    from keyless.ui.cursor_motion import GazeMotion

    motion = GazeMotion()
    motion.reset(50.0, 60.0)
    for _ in range(60):
        motion.update(400.0, 400.0, 1.0 / 120.0, active=False)
    assert (motion.x, motion.y) == (50.0, 60.0)
    assert motion.mode == "hold"
