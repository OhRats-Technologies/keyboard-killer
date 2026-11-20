"""Adaptive One-Euro filter tuned for pixel-space stereo gaze tracking."""

from __future__ import annotations

import math


def _smoothing_factor(dt: float, cutoff: float) -> float:
    tau = 1.0 / (2.0 * math.pi * max(1e-3, cutoff))
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """1D One-Euro filter with pixel-scale auto-normalization and jitter deadband."""

    def __init__(
        self,
        min_cutoff: float = 1.4,
        beta: float = 0.008,
        d_cutoff: float = 1.8,
        speed_deadzone: float = 140.0,
        max_single_step_px: float = 260.0,
    ) -> None:
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.speed_deadzone = float(speed_deadzone)
        self.max_single_step_px = float(max_single_step_px)
        self._x_prev: float | None = None
        self._x_raw_prev: float | None = None
        self._x_raw_prev2: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    def reset(self) -> None:
        self._x_prev = None
        self._x_raw_prev = None
        self._x_raw_prev2 = None
        self._dx_prev = 0.0
        self._t_prev = None

    def __call__(self, x: float, t_sec: float, external_speed: float | None = None) -> float:
        xf = float(x)
        tf = float(t_sec)
        if self._t_prev is None or self._x_prev is None or self._x_raw_prev is None:
            self._x_prev = xf
            self._x_raw_prev = xf
            self._x_raw_prev2 = xf
            self._dx_prev = 0.0
            self._t_prev = tf
            return xf

        dt = max(1e-4, min(0.25, tf - self._t_prev))
        self._t_prev = tf

        # Suppress isolated 1-frame landmark glitches unless confirmed by prior frame direction.
        step_from_filt = xf - self._x_prev
        prev_step = self._x_raw_prev - (self._x_raw_prev2 if self._x_raw_prev2 is not None else xf)
        if (
            abs(step_from_filt) > self.max_single_step_px
            and (step_from_filt * prev_step) <= 0.0
        ):
            xf_damped = self._x_prev + 0.35 * step_from_filt
        else:
            xf_damped = xf

        # Net 2-step directional velocity cancels out alternating +N/-N frame noise.
        if self._x_raw_prev2 is not None:
            net_dx = (xf_damped - self._x_raw_prev2) / (2.0 * dt)
        else:
            net_dx = (xf_damped - self._x_prev) / dt

        self._x_raw_prev2 = self._x_raw_prev
        self._x_raw_prev = xf

        alpha_d = _smoothing_factor(dt, self.d_cutoff)
        dx_hat = alpha_d * net_dx + (1.0 - alpha_d) * self._dx_prev
        self._dx_prev = dx_hat

        speed = abs(dx_hat) if external_speed is None else float(external_speed)

        # Auto-scale beta if caller supplies a [0..1]-normalized beta (e.g. 0.04) in pixel space.
        is_pixel_space = abs(xf) > 2.0 or abs(self._x_prev) > 2.0
        eff_beta = (self.beta * 0.18) if (is_pixel_space and self.beta >= 0.015) else self.beta
        deadzone = self.speed_deadzone if is_pixel_space else 0.15

        excess_speed = max(0.0, speed - deadzone)
        cutoff = self.min_cutoff + eff_beta * excess_speed
        alpha = _smoothing_factor(dt, cutoff)

        x_hat = alpha * xf_damped + (1.0 - alpha) * self._x_prev
        self._x_prev = x_hat
        return x_hat


class OneEuroFilter2D:
    """Anisotropic 2D One-Euro filter with joint saccade release and tighter Y noise damping."""

    def __init__(
        self,
        min_cutoff: float = 1.4,
        beta: float = 0.008,
        d_cutoff: float = 1.8,
        y_cutoff_scale: float = 0.72,
        speed_deadzone: float = 140.0,
    ) -> None:
        self.fx = OneEuroFilter(
            min_cutoff=min_cutoff,
            beta=beta,
            d_cutoff=d_cutoff,
            speed_deadzone=speed_deadzone,
            max_single_step_px=260.0,
        )
        self.fy = OneEuroFilter(
            min_cutoff=min_cutoff * y_cutoff_scale,
            beta=beta * 0.90,
            d_cutoff=d_cutoff,
            speed_deadzone=speed_deadzone * 1.15,
            max_single_step_px=220.0,
        )

    def reset(self) -> None:
        self.fx.reset()
        self.fy.reset()

    def __call__(self, x: float, y: float, t_sec: float) -> tuple[float, float]:
        joint_speed = math.hypot(self.fx._dx_prev, self.fy._dx_prev)
        use_joint = joint_speed if joint_speed > 550.0 else None
        out_x = self.fx(x, t_sec, external_speed=use_joint)
        out_y = self.fy(y, t_sec, external_speed=use_joint)
        return out_x, out_y
