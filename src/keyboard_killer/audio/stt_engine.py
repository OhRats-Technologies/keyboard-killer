"""Compatibility re-exports; STT engines live in ``keyboard_killer.voice.stt``."""

from keyboard_killer.voice.audio_io import UnifiedAudioService
from keyboard_killer.voice.stt.whisper import WhisperSTTEngine, _preload_cuda_libs

__all__ = ["UnifiedAudioService", "WhisperSTTEngine", "_preload_cuda_libs"]
