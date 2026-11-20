"""Sub-45ms plosive-resistant acoustic lip-pop / tongue-click detector."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PopEvalResult:
    """Telemetry from evaluating one 16ms audio chunk."""

    triggered: bool
    rejected_plosive: bool
    rms: float
    peak: float
    crest: float
    noise_floor: float
    muted: bool


class PopTriggerStateMachine:
    """Detects crisp lip-pops/tongue-clicks with pre/post silence gates & PTT mute lock.

    Guards against false positives from:
    - Spoken plosives ("p", "t", "k"): rejected by 32ms trailing-silence verification.
    - 1-sample electrical ticks / distant keyboard taps: rejected by `min_rms` energy floor.
    - Ongoing speech / rustling: rejected by `pre_rms` quietness requirement before the spike.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_samples: int = 256,
        min_peak: float = 0.25,
        min_rms: float = 0.032,
        peak_over_floor_ratio: float = 18.0,
        rms_over_floor_ratio: float = 5.0,
        min_crest: float = 2.85,
        trailing_chunks_required: int = 2,
        trailing_decay_ratio: float = 0.22,
        cooldown_ms: float = 320.0,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.chunk_samples = int(chunk_samples)
        self.min_peak = float(min_peak)
        self.min_rms = float(min_rms)
        self.peak_over_floor_ratio = float(peak_over_floor_ratio)
        self.rms_over_floor_ratio = float(rms_over_floor_ratio)
        self.min_crest = float(min_crest)
        self.trailing_chunks_required = int(trailing_chunks_required)
        self.trailing_decay_ratio = float(trailing_decay_ratio)
        chunk_ms = 1000.0 * self.chunk_samples / self.sample_rate
        self.cooldown_chunks = max(1, int(round(cooldown_ms / chunk_ms)))

        self._muted = False
        self._prev_sample = 0.0
        self._prev_chunk_rms = 0.0
        self._noise_floor = 0.005
        self._cooldown_left = 0

        self._pending_spike_rms: float | None = None
        self._trailing_rms: list[float] = []

        self.total_triggers = 0
        self.total_rejected_plosives = 0

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> None:
        """Enable or disable the push-to-talk / STT mute lock."""
        self._muted = bool(muted)
        if self._muted:
            self._pending_spike_rms = None
            self._trailing_rms.clear()

    def _highpass(self, chunk: np.ndarray) -> np.ndarray:
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return arr
        out = np.empty_like(arr)
        out[0] = arr[0] - 0.85 * self._prev_sample
        if arr.size > 1:
            out[1:] = arr[1:] - 0.85 * arr[:-1]
        self._prev_sample = float(arr[-1])
        return out

    def process_chunk(self, pcm_chunk: np.ndarray) -> PopEvalResult:
        """Process one audio block and return whether a pop click was confirmed."""
        hp = self._highpass(pcm_chunk)
        if hp.size == 0:
            return PopEvalResult(False, False, 0.0, 0.0, 0.0, self._noise_floor, self._muted)

        rms = float(np.sqrt(np.mean(hp * hp)))
        peak = float(np.max(np.abs(hp)))
        crest = peak / max(rms, 1e-6)
        pre_rms = self._prev_chunk_rms
        self._prev_chunk_rms = rms

        if self._muted:
            return PopEvalResult(False, False, rms, peak, crest, self._noise_floor, True)

        if self._cooldown_left > 0:
            self._cooldown_left -= 1
            if self._cooldown_left == 0 and rms < self._noise_floor * 3.0:
                self._noise_floor = max(0.002, 0.94 * self._noise_floor + 0.06 * rms)
            return PopEvalResult(False, False, rms, peak, crest, self._noise_floor, False)

        # Verify 32ms trailing silence after a candidate transient.
        if self._pending_spike_rms is not None:
            self._trailing_rms.append(rms)
            if len(self._trailing_rms) < self.trailing_chunks_required:
                return PopEvalResult(False, False, rms, peak, crest, self._noise_floor, False)

            max_trail = max(self._trailing_rms)
            silence_limit = max(
                self._noise_floor * 4.0,
                self._pending_spike_rms * self.trailing_decay_ratio,
            )
            self._pending_spike_rms = None
            self._trailing_rms.clear()

            if max_trail <= silence_limit:
                self.total_triggers += 1
                self._cooldown_left = self.cooldown_chunks
                return PopEvalResult(True, False, rms, peak, crest, self._noise_floor, False)

            self.total_rejected_plosives += 1
            self._cooldown_left = max(3, self.cooldown_chunks // 3)
            return PopEvalResult(False, True, rms, peak, crest, self._noise_floor, False)

        # Idle state: require quietness right before the spike + sufficient peak, RMS, and crest
        peak_thresh = max(self.min_peak, self._noise_floor * self.peak_over_floor_ratio)
        rms_thresh = max(self.min_rms, self._noise_floor * self.rms_over_floor_ratio)
        pre_quiet_limit = max(0.020, self._noise_floor * 4.0)

        if (
            peak >= peak_thresh
            and rms >= rms_thresh
            and crest >= self.min_crest
            and pre_rms <= pre_quiet_limit
        ):
            self._pending_spike_rms = rms
            self._trailing_rms.clear()
            return PopEvalResult(False, False, rms, peak, crest, self._noise_floor, False)

        if rms < self._noise_floor * 4.0:
            self._noise_floor = max(0.002, 0.96 * self._noise_floor + 0.04 * rms)

        return PopEvalResult(False, False, rms, peak, crest, self._noise_floor, False)
