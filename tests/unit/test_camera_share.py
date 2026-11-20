"""Tests for the opt-in PipeWire camera-sharing backend."""

from __future__ import annotations

import io

import numpy as np
import pytest

from keyless.platform.linux import pipewire


def test_pipewire_capture_reads_fixed_bgr_frames(monkeypatch) -> None:
    width, height = pipewire.PIPEWIRE_SIZE
    frame = bytes(width * height * 3)
    commands: list[list[str]] = []

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(frame + frame)
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        commands.append(list(command))
        return FakeProc()

    monkeypatch.setattr(pipewire, "_pipewire_src_available", lambda: True)
    monkeypatch.setattr(pipewire, "resolve_by_id_device", lambda _path: "/dev/video0")
    monkeypatch.setattr(pipewire.subprocess, "Popen", fake_popen)

    cap, report = pipewire.open_pipewire_capture("/dev/v4l/by-id/cam")
    try:
        ok, image = cap.read()
        assert ok and image is not None
        assert image.shape == (height, width, 3)
        assert image.dtype == np.uint8
        assert report.width == width and report.height == height
        assert "pipewiresrc" in commands[0]
        assert "fdsink" in commands[0]
    finally:
        cap.release()


def test_pipewire_capture_requires_gstreamer(monkeypatch) -> None:
    monkeypatch.setattr(pipewire, "_pipewire_src_available", lambda: False)
    with pytest.raises(RuntimeError, match="PipeWire"):
        pipewire.PipeWireCapture()
