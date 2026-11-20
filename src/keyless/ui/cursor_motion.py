"""Smooth, fixation-aware cursor motion for the gaze overlay.

The vision loop publishes a 30 Hz target.  Drawing that target directly looks
steppy and amplifies landmark noise.  This module turns it into a continuous
60 Hz cursor with a critically damped spring, a median outlier filter, and a
fixation hold: small unstable movements settle instead of shivering.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class MotionSettings:
    stiffness: float = 150.0
    damping_ratio: float = 1.0
    max_speed: float = 7000.0
    fixation_radius: float = 26.0
    fixation_break_radius: float = 48.0
    fixation_dwell_s: float = 0.13
    search_spread: float = 95.0
    search_stiffness_scale: float = 0.48
    search_damping_scale: float = 1.55
    history_seconds: float = 0.16
    median_samples: int = 5


class GazeMotion:
    """A 2D spring that holds fixations and damps searching/unfocused gaze."""

    def __init__(self, settings: MotionSettings | None = None) -> None:
        self.settings = settings or MotionSettings()
        self.x = 0.0
        self.y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.mode = "idle"
        self._t = 0.0
        self._samples: deque[tuple[float, float, float]] = deque(maxlen=24)
        self._fixation: tuple[float, float] | None = None
        self._fixation_since = 0.0

    def reset(self, x: float, y: float, t: float = 0.0) -> None:
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self._t = float(t)
        self._samples.clear()
        self._fixation = None
        self._fixation_since = 0.0
        self.mode = "idle"

    def _median_target(self) -> tuple[float, float]:
        recent = list(self._samples)[-self.settings.median_samples :]
        xs = sorted(sample[1] for sample in recent)
        ys = sorted(sample[2] for sample in recent)
        mid = len(xs) // 2
        return (xs[mid], ys[mid])

    def _dispersion(self, cx: float, cy: float) -> float:
        if not self._samples:
            return 0.0
        return max(
            math.hypot(x - cx, y - cy) for _, x, y in list(self._samples)[-8:]
        )

    def update(
        self,
        target_x: float,
        target_y: float,
        dt: float,
        *,
        active: bool = True,
        blinking: bool = False,
    ) -> tuple[float, float, str]:
        """Advance the spring and return the drawn position plus its mode."""
        dt = max(1e-4, min(0.08, float(dt)))
        self._t += dt
        now = self._t

        if not active:
            self.vx *= 0.72
            self.vy *= 0.72
            self.mode = "hold"
            return self.x, self.y, self.mode
        if blinking:
            self.vx *= 0.86
            self.vy *= 0.86
            self.mode = "blink"
            return self.x, self.y, self.mode

        self._samples.append((now, float(target_x), float(target_y)))
        while self._samples and now - self._samples[0][0] > self.settings.history_seconds:
            self._samples.popleft()

        median_x, median_y = self._median_target()
        spread = self._dispersion(median_x, median_y)
        s = self.settings

        if self._fixation is None:
            if spread <= s.fixation_radius and len(self._samples) >= 3:
                if self._fixation_since <= 0.0:
                    self._fixation_since = now
                if now - self._fixation_since >= s.fixation_dwell_s:
                    self._fixation = (median_x, median_y)
        else:
            fx, fy = self._fixation
            if math.hypot(median_x - fx, median_y - fy) > s.fixation_break_radius:
                self._fixation = None
                self._fixation_since = now

        if self._fixation is not None:
            # Hold the center of the fixation instead of following landmark jitter.
            self._fixation = (
                0.9 * self._fixation[0] + 0.1 * median_x,
                0.9 * self._fixation[1] + 0.1 * median_y,
            )
            tx, ty = self._fixation
            mode = "fixation"
            stiffness = s.stiffness * 1.15
            damping_scale = 1.0
        elif spread > s.search_spread:
            # A large, coherent jump is a saccade: follow it quickly.
            tx, ty = median_x, median_y
            mode = "saccade"
            stiffness = s.stiffness
            damping_scale = 0.92
        elif spread > s.fixation_radius:
            # Small, dispersed motion usually means searching or an unfocused
            # eye.  Follow it with more damping and a softer spring.
            tx, ty = median_x, median_y
            mode = "searching"
            stiffness = s.stiffness * s.search_stiffness_scale
            damping_scale = s.search_damping_scale
        else:
            tx, ty = median_x, median_y
            mode = "tracking"
            stiffness = s.stiffness
            damping_scale = 1.0

        damping = 2.0 * math.sqrt(stiffness) * s.damping_ratio * damping_scale
        ax = stiffness * (tx - self.x) - damping * self.vx
        ay = stiffness * (ty - self.y) - damping * self.vy
        self.vx += ax * dt
        self.vy += ay * dt
        speed = math.hypot(self.vx, self.vy)
        if speed > s.max_speed:
            scale = s.max_speed / speed
            self.vx *= scale
            self.vy *= scale
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.mode = mode
        return self.x, self.y, mode


__all__ = ["GazeMotion", "MotionSettings"]
