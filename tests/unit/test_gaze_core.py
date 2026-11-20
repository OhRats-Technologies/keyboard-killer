"""Headless tests for the gaze view, demo path, and speech smoke test."""

from __future__ import annotations

import time

import numpy as np
import pytest

pytest.importorskip("PySide6")

from keyboard_killer.audio.quick_test import DEFAULT_SECONDS, run_speech_test
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.ipc.state import FLAG_BLINKING, FLAG_FACE_DETECTED, GazeState
from keyboard_killer.ui.gaze_art import demo_gaze
from keyboard_killer.ui.gaze_source import gaze_state_sample
from keyboard_killer.ui.screen_cursor import ScreenCursor
from keyboard_killer.voice.stt.base import SttResult


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

    monkeypatch.setattr("keyboard_killer.audio.quick_test.record_clip", fake_record)
    stages: list[str] = []
    result = run_speech_test(AppConfig(), engine=FakeEngine(), on_stage=stages.append)
    assert result.transcript == "click send"
    assert result.latency_ms == 18.5
    assert result.recorded_s == pytest.approx(DEFAULT_SECONDS)
    assert result.peak == pytest.approx(0.2)
    assert result.has_speech
    assert stages == ["recording", "transcribing"]
