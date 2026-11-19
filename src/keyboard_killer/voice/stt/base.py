"""Speech-to-text engine contract (D10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class SttResult:
    """Transcript plus the confidence signals the voice gates consume."""

    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    latency_ms: float = 0.0

    @property
    def confidence(self) -> float:
        import math

        return float(math.exp(max(-2.0, min(0.0, self.avg_logprob))))


@dataclass(frozen=True)
class SttStatus:
    name: str
    ready: bool
    device: str
    compute_type: str
    model: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ready": self.ready,
            "device": self.device,
            "compute_type": self.compute_type,
            "model": self.model,
            "detail": self.detail,
        }


@runtime_checkable
class SttEngine(Protocol):
    """Offline recognition on push-to-talk release or VAD endpoint."""

    name: str

    def transcribe(self, audio_f32: np.ndarray) -> str: ...

    def transcribe_detailed(self, audio_f32: np.ndarray) -> SttResult: ...

    def status(self) -> SttStatus: ...

    def close(self) -> None: ...
