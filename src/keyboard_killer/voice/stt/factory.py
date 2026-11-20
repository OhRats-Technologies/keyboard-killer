"""Parakeet STT selection and status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from keyboard_killer.config.paths import AppPaths, current_paths
from keyboard_killer.config.schema import AppConfig
from keyboard_killer.models.catalog import DEFAULT_STT_ASSET_ID, get_asset
from keyboard_killer.models.downloader import DownloadError, DownloadProgress, download_asset
from keyboard_killer.models.store import ModelStore
from keyboard_killer.voice.stt.base import SttEngine
from keyboard_killer.voice.stt.parakeet import CUDA_PROVIDERS, DEFAULT_PROVIDERS, ParakeetSttEngine

ProgressCallback = Callable[[DownloadProgress], None]


class SttModelMissing(RuntimeError):
    """The app-owned Parakeet files are not downloaded or verified."""


@dataclass
class SttSelection:
    engine: SttEngine
    requested: str = "parakeet"

    @property
    def fell_back(self) -> bool:
        return False


def cuda_available() -> bool:
    try:
        import onnxruntime

        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def _providers(config: AppConfig) -> tuple[str, ...]:
    if (config.stt_provider or "cpu").lower() == "cuda" and cuda_available():
        return CUDA_PROVIDERS
    return DEFAULT_PROVIDERS


def create_stt_engine(
    config: AppConfig,
    *,
    paths: AppPaths | None = None,
    allow_download: bool = False,
    progress: ProgressCallback | None = None,
) -> SttSelection:
    """Load the pinned Parakeet model; never substitute another engine."""
    asset = get_asset(config.stt_model_id or DEFAULT_STT_ASSET_ID)
    store = ModelStore(paths or current_paths())
    status = store.status(asset)
    if not status.ready and allow_download:
        try:
            download_asset(asset, paths=paths, store=store, progress=progress)
            status = store.status(asset, verify_hashes=True)
        except DownloadError as exc:
            raise SttModelMissing(f"Speech model download failed: {exc}") from exc
    if not status.ready:
        raise SttModelMissing(
            f"Speech model is {status.state.value}. Run `keyboard-killer models download stt`."
        )
    engine = ParakeetSttEngine(
        store.asset_dir(asset),
        quantization=asset.quantization,
        providers=_providers(config),
    )
    return SttSelection(engine=engine)


def stt_report(config: AppConfig, *, paths: AppPaths | None = None) -> dict[str, object]:
    """Cheap status for `doctor` and the UI; does not load the engine."""
    asset = get_asset(config.stt_model_id or DEFAULT_STT_ASSET_ID)
    status = ModelStore(paths or current_paths()).status(asset)
    return {
        "requested": "parakeet",
        "selected": "parakeet" if status.ready else "missing",
        "parakeet": status.as_dict(),
        "cuda_available": cuda_available(),
    }
