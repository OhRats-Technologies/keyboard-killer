"""PipeWire camera sharing through a small GStreamer capture process.

V4L2 devices are usually exclusive, so opening ``/dev/videoN`` blocks Chrome
and every other camera client.  PipeWire owns the device instead and fans the
stream out to multiple clients.  OpenCV here is built without GStreamer, so
this backend runs ``gst-launch-1.0`` and reads fixed-size BGR frames from its
stdout.  It is intentionally opt-in: exposure locks are not available and the
portal negotiates 640x480 on this desktop.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import numpy as np

from keyless.platform.linux.v4l2 import CameraLockReport, resolve_by_id_device

PIPEWIRE_SIZE = (640, 480)
PIPEWIRE_FPS = 30


def _die_with_parent() -> None:
    """Ask the kernel to terminate gst-launch if the daemon is killed."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    except Exception:
        pass


def pipewire_available() -> bool:
    return bool(shutil.which("gst-launch-1.0")) and bool(shutil.which("gst-inspect-1.0"))


def _pipewire_src_available() -> bool:
    if not pipewire_available():
        return False
    result = subprocess.run(
        ["gst-inspect-1.0", "pipewiresrc"],
        capture_output=True,
        text=True,
        timeout=2.0,
    )
    return result.returncode == 0


class PipeWireCapture:
    """A cv2.VideoCapture-shaped reader fed by ``pipewiresrc ! fdsink``."""

    def __init__(self, *, width: int = PIPEWIRE_SIZE[0], height: int = PIPEWIRE_SIZE[1]) -> None:
        if not _pipewire_src_available():
            raise RuntimeError("PipeWire camera capture needs gst-launch-1.0 with pipewiresrc.")
        self.width = int(width)
        self.height = int(height)
        self._frame_bytes = self.width * self.height * 3
        command = [
            "gst-launch-1.0",
            "-q",
            "pipewiresrc",
            "!",
            "videoconvert",
            "!",
            f"video/x-raw,format=BGR,width={self.width},height={self.height},"
            f"framerate={PIPEWIRE_FPS}/1",
            "!",
            "fdsink",
            "sync=false",
        ]
        self._proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            bufsize=self._frame_bytes,
            preexec_fn=_die_with_parent,
        )

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def isOpened(self) -> bool:  # noqa: N802 - cv2 compatibility
        return self.alive

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._proc.stdout is None:
            return False, None
        data = self._proc.stdout.read(self._frame_bytes)
        if not data or len(data) < self._frame_bytes:
            return False, None
        frame = np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3).copy()
        return True, frame

    def release(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1.0)


@dataclass(frozen=True)
class PipeWireStatus:
    resolved_dev: str
    by_id_path: str

    def report(self) -> CameraLockReport:
        return CameraLockReport(
            by_id_path=self.by_id_path,
            resolved_dev=self.resolved_dev,
            applied={},
            unsupported=["exposure", "gain", "manual-fps"],
            fourcc="BGR",
            width=PIPEWIRE_SIZE[0],
            height=PIPEWIRE_SIZE[1],
            fps=float(PIPEWIRE_FPS),
        )


def open_pipewire_capture(
    by_id_path: str,
    *,
    width: int = PIPEWIRE_SIZE[0],
    height: int = PIPEWIRE_SIZE[1],
) -> tuple[PipeWireCapture, CameraLockReport]:
    """Open the default PipeWire video source and return a cv2-shaped capture."""
    resolved = resolve_by_id_device(by_id_path)
    cap = PipeWireCapture(width=width, height=height)
    # The portal's default source is the same physical camera on this desktop;
    # keep the resolved path in the report so diagnostics still identify it.
    status = PipeWireStatus(resolved_dev=resolved, by_id_path=by_id_path)
    return cap, status.report()


def pipewire_note() -> str:
    if not pipewire_available():
        return "PipeWire sharing needs GStreamer's gst-launch-1.0 and pipewiresrc."
    return "PipeWire sharing uses the portal camera at 640x480; exposure locks are unavailable."


__all__ = [
    "PIPEWIRE_SIZE",
    "PipeWireCapture",
    "open_pipewire_capture",
    "pipewire_available",
    "pipewire_note",
]
