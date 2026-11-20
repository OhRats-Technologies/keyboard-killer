"""Bounded live-gaze reader for UI surfaces.

The vision daemon publishes a seqlock shared-memory snapshot.  The UI must
never assume it exists: the reader attaches lazily, reports staleness, and
re-attaches after the daemon restarts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from keyboard_killer.ipc.state import (
    FLAG_BLINKING,
    FLAG_FACE_DETECTED,
    SHM_NAME,
    GazeState,
    SharedGazeReader,
)

STALE_AFTER_S = 0.45
RETRY_ATTACH_S = 1.0


@dataclass(frozen=True)
class GazeSample:
    x: float
    y: float
    face: bool
    blinking: bool
    age_s: float
    seq: int

    @property
    def live(self) -> bool:
        return self.age_s <= STALE_AFTER_S


class LiveGaze:
    """Attach to the daemon's gaze shm on demand and close cleanly."""

    def __init__(self, shm_name: str = SHM_NAME) -> None:
        self.shm_name = shm_name
        self._reader: SharedGazeReader | None = None
        self._next_attach = 0.0
        self._attached_at = 0.0
        self._last_error = ""

    def read(self) -> GazeSample | None:
        now = time.monotonic()
        if self._reader is None:
            if now < self._next_attach:
                return None
            try:
                self._reader = SharedGazeReader(self.shm_name)
                self._attached_at = now
            except (FileNotFoundError, OSError) as exc:
                self._last_error = str(exc)
                self._next_attach = now + RETRY_ATTACH_S
                return None
        try:
            state = self._reader.read()
        except (BufferError, OSError, ValueError) as exc:
            self._last_error = str(exc)
            self._detach()
            return None
        if state is None or state.timestamp_ns <= 0:
            if now - self._attached_at > 2.0:
                self._detach()
            return None
        age = (time.monotonic_ns() - state.timestamp_ns) / 1_000_000_000.0
        if age > 4.0:
            # The daemon stopped or restarted; drop the stale mapping.
            self._detach()
            return None
        return GazeSample(
            x=state.x,
            y=state.y,
            face=bool(state.flags & FLAG_FACE_DETECTED),
            blinking=bool(state.flags & FLAG_BLINKING),
            age_s=max(0.0, age),
            seq=state.seq,
        )

    @property
    def last_error(self) -> str:
        return self._last_error

    def _detach(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
        self._reader = None
        self._next_attach = time.monotonic() + RETRY_ATTACH_S

    def close(self) -> None:
        self._detach()


def gaze_state_sample(state: GazeState | None, now_ns: int | None = None) -> GazeSample | None:
    """Pure helper for tests and non-shm producers."""
    if state is None or state.timestamp_ns <= 0:
        return None
    now = time.monotonic_ns() if now_ns is None else now_ns
    age = max(0.0, (now - state.timestamp_ns) / 1_000_000_000.0)
    return GazeSample(
        x=state.x,
        y=state.y,
        face=bool(state.flags & FLAG_FACE_DETECTED),
        blinking=bool(state.flags & FLAG_BLINKING),
        age_s=age,
        seq=state.seq,
    )


__all__ = ["GazeSample", "LiveGaze", "STALE_AFTER_S", "gaze_state_sample"]
