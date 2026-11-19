"""faster-whisper STT engine (fallback; CPU int8 by default)."""

from __future__ import annotations

import ctypes
import logging
import os
import time

import numpy as np

from keyboard_killer.voice.stt.base import SttStatus, SttResult

logger = logging.getLogger(__name__)

_CUDA_LIB_SEARCH_DIRS = (
    "/usr/local/lib/ollama/cuda_v12",
    "/usr/local/cuda/lib64",
    "/usr/local/cuda-12/lib64",
)


def _preload_cuda_libs() -> None:
    """Opt-in helper for CUDA faster-whisper installs (never on the default path)."""
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
    """faster-whisper speech-to-text engine; CPU int8 unless CUDA is requested."""

    name = "faster-whisper"

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
        preload_cuda: bool = False,
        warmup: bool = True,
    ) -> None:
        if preload_cuda:
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
                logger.warning("Whisper %s init failed (%s); falling back to CPU int8.", device, exc)
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
            else:
                raise

        if warmup:
            # Warm kernels once so the first utterance is not an outlier.
            self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe_detailed(self, audio_f32: np.ndarray) -> SttResult:
        arr = np.asarray(audio_f32, dtype=np.float32).reshape(-1)
        if arr.size < 2800:  # <175ms
            return SttResult(text="")
        if float(np.max(np.abs(arr))) < 1e-4:
            return SttResult(text="")

        started = time.perf_counter()
        segments, _info = self._model.transcribe(
            arr,
            language=self.language,
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        seg_list = list(segments)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        text = " ".join(seg.text.strip() for seg in seg_list if seg.text.strip()).strip()
        if not seg_list:
            return SttResult(text=text, latency_ms=elapsed_ms)
        no_speech = sum(float(s.no_speech_prob) for s in seg_list) / len(seg_list)
        logprob = sum(float(s.avg_logprob) for s in seg_list) / len(seg_list)
        return SttResult(
            text=text,
            no_speech_prob=no_speech,
            avg_logprob=logprob,
            latency_ms=elapsed_ms,
        )

    def transcribe(self, audio_f32: np.ndarray) -> str:
        """Transcribe 16kHz mono float32 audio into clean text."""
        return self.transcribe_detailed(audio_f32).text

    def status(self) -> SttStatus:
        return SttStatus(
            name=self.name,
            ready=True,
            device=self.device,
            compute_type=self.compute_type,
            model=self.model_size,
            detail="Automatic fallback engine.",
        )

    def close(self) -> None:
        self._model = None
