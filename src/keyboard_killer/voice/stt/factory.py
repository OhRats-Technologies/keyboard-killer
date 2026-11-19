"""STT engine selection and fallback (D10)."""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from typing import Callable

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.models.catalog import DEFAULT_STT_ASSET_ID, get_asset
from keyboard_killer.models.downloader import (
    DownloadError,
    DownloadProgress,
    download_asset,
)
from keyboard_killer.models.store import ModelState, ModelStore
from keyboard_killer.voice.stt.base import SttEngine
from keyboard_killer.voice.stt.parakeet import CUDA_PROVIDERS, DEFAULT_PROVIDERS, ParakeetSttEngine
from keyboard_killer.voice.stt.whisper import WhisperSTTEngine

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[DownloadProgress], None]


@dataclass
class SttSelection:
    engine: SttEngine
    requested: str
    fallback_reason: str = ""

    @property
    def fell_back(self) -> bool:
        return bool(self.fallback_reason)


def cuda_available() -> bool:
    try:
        import onnxruntime

        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def _parakeet_providers(config: AppConfig) -> tuple[str, ...]:
    if (config.stt_provider or "cpu").lower() == "cuda" and cuda_available():
        return CUDA_PROVIDERS
    return DEFAULT_PROVIDERS


def _whisper_engine(config: AppConfig) -> WhisperSTTEngine:
    use_cuda = (config.stt_provider or "cpu").lower() == "cuda" and cuda_available()
    return WhisperSTTEngine(
        model_size=config.whisper_model,
        device="cuda" if use_cuda else "cpu",
        compute_type="float16" if use_cuda else "int8",
        preload_cuda=use_cuda,
    )


def create_stt_engine(
    config: AppConfig,
    *,
    paths: AppPaths | None = None,
    allow_download: bool = False,
    progress: ProgressCallback | None = None,
) -> SttSelection:
    """Load Parakeet when its pinned files are ready, otherwise fall back to Whisper.

    A missing or corrupt model never blocks the daemon: the fallback is reported
    to the caller so the CLI and onboarding can show one action.
    """
    requested = (config.stt_engine or "auto").strip().lower()
    if requested not in {"auto", "parakeet", "whisper"}:
        raise ValueError(f"Unsupported STT engine: {config.stt_engine!r}")

    reason = ""
    if requested in {"auto", "parakeet"}:
        asset = get_asset(config.stt_model_id or DEFAULT_STT_ASSET_ID)
        store = ModelStore(paths or current_paths())
        status = store.status(asset)
        if not status.ready and allow_download:
            try:
                download_asset(asset, paths=paths, store=store, progress=progress)
                status = store.status(asset, verify_hashes=True)
            except DownloadError as exc:
                reason = f"Parakeet download failed: {exc}"
                logger.warning(reason)
        if status.ready:
            try:
                engine = ParakeetSttEngine(
                    store.asset_dir(asset),
                    quantization=asset.quantization,
                    providers=_parakeet_providers(config),
                )
                return SttSelection(engine=engine, requested=requested)
            except Exception as exc:
                reason = f"Parakeet load failed: {exc}"
                logger.warning(reason)
        elif not reason:
            reason = f"Parakeet is {status.state.value}: {status.detail}"
            if status.state is ModelState.MISSING:
                reason += " Run `keyboard-killer models download stt`."
        if requested == "parakeet":
            logger.warning("Falling back to faster-whisper: %s", reason)

    engine = _whisper_engine(config)
    return SttSelection(engine=engine, requested=requested, fallback_reason=reason)


def stt_report(config: AppConfig, *, paths: AppPaths | None = None) -> dict[str, object]:
    """Cheap status for `doctor` and the UI; does not load either engine."""
    asset = get_asset(config.stt_model_id or DEFAULT_STT_ASSET_ID)
    store = ModelStore(paths or current_paths())
    status = store.status(asset)
    whisper = importlib.util.find_spec("faster_whisper") is not None
    requested = (config.stt_engine or "auto").lower()
    selected = "parakeet" if status.ready else ("whisper" if whisper else "unavailable")
    return {
        "requested": requested,
        "selected": selected,
        "parakeet": status.as_dict(),
        "whisper": {
            "available": whisper,
            "model": config.whisper_model,
            "device": config.stt_provider,
        },
        "cuda_available": cuda_available(),
    }
