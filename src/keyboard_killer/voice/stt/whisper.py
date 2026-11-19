"""faster-whisper STT engine (fallback; CPU int8 by default)."""

from __future__ import annotations

import ctypes
import logging
import os

import numpy as np

logger = logging.getLogger(__name__)


_CUDA_LIB_SEARCH_DIRS = (
    "/usr/local/lib/ollama/cuda_v12",
    "/usr/local/cuda/lib64",
    "/usr/local/cuda-12/lib64",
)


def _preload_cuda_libs() -> None:
    """Preload system/ollama CUDA 12 cuBLAS libraries so ctranslate2 finds them automatically."""
    loaded_names: set[str] = set()
    for base_dir in _CUDA_LIB_SEARCH_DIRS:
        if not os.path.isdir(base_dir):
            continue
        for lib_name in ("libcublasLt.so.12", "libcublas.so.12"):
            if lib_name in loaded_names:
                continue
            candidate = os.path.join(base_dir, lib_name)
            if os.path.exists(candidate):
                try:
                    ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
                    loaded_names.add(lib_name)
                except OSError as exc:
                    logger.debug("Could not preload %s: %s", candidate, exc)


class WhisperSTTEngine:
    """VRAM-resident faster-whisper speech-to-text engine."""

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "en",
    ) -> None:
        _preload_cuda_libs()
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self.language = language
        self.device = device
        self.compute_type = compute_type

        try:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as exc:
            if device != "cpu":
                logger.warning("Whisper CUDA init failed (%s); falling back to CPU int8.", exc)
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
            else:
                raise

        # Warm up CUDA/cuBLAS kernels once at startup so first utterance is fast.
        self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe(self, audio_f32: np.ndarray) -> str:
        """Transcribe 16kHz mono float32 audio into clean text."""
        arr = np.asarray(audio_f32, dtype=np.float32).reshape(-1)
        if arr.size < 2800:  # <175ms
            return ""
        if float(np.max(np.abs(arr))) < 1e-4:
            return ""
        segments, _ = self._model.transcribe(
            arr,
            language=self.language,
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()
