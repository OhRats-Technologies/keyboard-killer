"""NVIDIA Parakeet TDT 0.6B v2 via onnx-asr (CPU int8 default, CUDA opt-in)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Sequence

import numpy as np

from keyboard_killer.voice.stt.base import SttStatus, SttResult

logger = logging.getLogger(__name__)

DEFAULT_PROVIDERS = ("CPUExecutionProvider",)
CUDA_PROVIDERS = ("CUDAExecutionProvider", "CPUExecutionProvider")


class ParakeetSttEngine:
    """Offline English recognition from the pinned local ONNX export."""

    name = "parakeet-tdt-0.6b-v2"

    def __init__(
        self,
        model_dir: Path | str,
        *,
        quantization: str = "int8",
        providers: Sequence[str] | None = None,
        warmup: bool = True,
    ) -> None:
        import onnx_asr

        self.model_dir = Path(model_dir)
        self.quantization = quantization
        self.compute_type = quantization
        self.providers = tuple(providers or DEFAULT_PROVIDERS)
        self.device = "cuda" if "CUDAExecutionProvider" in self.providers else "cpu"
        self._model = onnx_asr.load_model(
            "nemo-parakeet-tdt-0.6b-v2",
            path=str(self.model_dir),
            quantization=quantization,
            providers=list(self.providers),
        )
        if warmup:
            self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe_detailed(self, audio_f32: np.ndarray) -> SttResult:
        arr = np.asarray(audio_f32, dtype=np.float32).reshape(-1)
        if arr.size < 2800:  # <175ms
            return SttResult(text="")
        if float(np.max(np.abs(arr))) < 1e-4:
            return SttResult(text="")

        started = time.perf_counter()
        text = str(self._model.recognize(arr, sample_rate=16000) or "").strip()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        # Parakeet does not expose Whisper's no-speech/logprob metadata.  The VAD
        # endpointing and the lexical gates still apply in the listener.
        return SttResult(text=text, latency_ms=elapsed_ms)

    def transcribe(self, audio_f32: np.ndarray) -> str:
        return self.transcribe_detailed(audio_f32).text

    def status(self) -> SttStatus:
        return SttStatus(
            name=self.name,
            ready=True,
            device=self.device,
            compute_type=self.quantization,
            model=str(self.model_dir),
            detail="App-owned pinned model.",
        )

    def close(self) -> None:
        self._model = None
