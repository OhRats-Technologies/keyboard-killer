"""Microphone-to-Parakeet smoke test used by the Models page."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from keyboard_killer.config.paths import AppPaths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.voice.stt.base import SttEngine

StageCallback = Callable[[str], None]

DEFAULT_SECONDS = 4.0
SAMPLE_RATE = 16000


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int


@dataclass(frozen=True)
class MicLevel:
    peak: float
    rms: float
    seconds: float

    @property
    def heard(self) -> bool:
        return self.peak >= 0.01


@dataclass(frozen=True)
class SpeechTestResult:
    transcript: str
    latency_ms: float
    recorded_s: float
    sample_rate: int
    peak: float
    engine: str

    @property
    def has_speech(self) -> bool:
        return self.peak >= 0.01 and bool(self.transcript)


def input_devices() -> list[InputDevice]:
    """List PortAudio input devices without failing when the backend is absent."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        result: list[InputDevice] = []
        for index, device in enumerate(devices):
            channels = int(device.get("max_input_channels", 0))
            if channels > 0:
                result.append(InputDevice(index, str(device.get("name", "")), channels))
        return result
    except Exception:
        return []


def check_microphone(
    device: str | int | None = None,
    *,
    seconds: float = 1.5,
) -> MicLevel:
    """Record a short clip and report whether the selected input carries signal."""
    audio = record_clip(seconds, device=device)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
    return MicLevel(peak=peak, rms=rms, seconds=seconds)


def record_clip(
    seconds: float = DEFAULT_SECONDS,
    *,
    sample_rate: int = SAMPLE_RATE,
    device: str | int | None = None,
) -> np.ndarray:
    """Blocking monophonic capture; sounddevice owns the PortAudio stream."""
    import sounddevice as sd

    frames = max(1, int(round(seconds * sample_rate)))
    audio = sd.rec(
        frames,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


class LiveRecorder:
    """PortAudio InputStream that exposes a live RMS level while recording."""

    def __init__(
        self,
        device: str | int | None = None,
        *,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self.device = device
        self.sample_rate = int(sample_rate)
        self.level = 0.0
        self._chunks: list[np.ndarray] = []
        self._stop = threading.Event()

    def _callback(self, indata, frames, time_info, status) -> None:
        chunk = indata[:, 0].copy()
        self._chunks.append(chunk)
        self.level = float(np.sqrt(np.mean(chunk * chunk)))

    def record(self, seconds: float) -> np.ndarray:
        import sounddevice as sd

        self._chunks = []
        self.level = 0.0
        self._stop.clear()
        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
            blocksize=256,
        ):
            deadline = time.monotonic() + float(seconds)
            while not self._stop.is_set() and time.monotonic() < deadline:
                sd.sleep(50)
        self.level = 0.0
        return (
            np.concatenate(self._chunks)
            if self._chunks
            else np.zeros(0, dtype=np.float32)
        )

    def stop(self) -> None:
        self._stop.set()


def run_speech_test(
    config: AppConfig,
    *,
    paths: AppPaths | None = None,
    seconds: float = DEFAULT_SECONDS,
    engine: SttEngine | None = None,
    recorder: LiveRecorder | None = None,
    on_stage: StageCallback | None = None,
) -> SpeechTestResult:
    """Load the pinned model, record one clip, and transcribe it locally."""
    stage = on_stage or (lambda _name: None)
    if engine is None:
        from keyboard_killer.voice.stt.factory import create_stt_engine

        stage("loading")
        engine = create_stt_engine(config, paths=paths).engine
    stage("recording")
    audio = (
        recorder.record(seconds)
        if recorder is not None
        else record_clip(seconds, device=config.audio_device)
    )
    stage("transcribing")
    detail = engine.transcribe_detailed(audio)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    return SpeechTestResult(
        transcript=detail.text.strip(),
        latency_ms=float(detail.latency_ms),
        recorded_s=len(audio) / SAMPLE_RATE,
        sample_rate=SAMPLE_RATE,
        peak=peak,
        engine=engine.name,
    )


__all__ = [
    "DEFAULT_SECONDS",
    "InputDevice",
    "LiveRecorder",
    "MicLevel",
    "SAMPLE_RATE",
    "SpeechTestResult",
    "check_microphone",
    "input_devices",
    "record_clip",
    "run_speech_test",
]
