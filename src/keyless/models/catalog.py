"""Pinned, app-owned model catalog (D2/D10).

Every entry pins a repository revision and the exact bytes of every file.  The
downloader verifies size and SHA-256 before promoting a partial file.
"""

from __future__ import annotations

from dataclasses import dataclass

HF_BASE = "https://huggingface.co"

PARAKEET_TDT_V2_ID = "parakeet-tdt-0.6b-v2"


@dataclass(frozen=True)
class ModelFile:
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ModelAsset:
    id: str
    display_name: str
    engine: str
    quantization: str
    revision: str
    repo: str
    license: str
    attribution: str
    files: tuple[ModelFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    def url(self, file: ModelFile) -> str:
        return f"{HF_BASE}/{self.repo}/resolve/{self.revision}/{file.name}"

    def file(self, name: str) -> ModelFile:
        for item in self.files:
            if item.name == name:
                return item
        raise KeyError(name)


PARAKEET_TDT_0_6B_V2 = ModelAsset(
    id=PARAKEET_TDT_V2_ID,
    display_name="Parakeet TDT 0.6B v2 (English, int8)",
    engine="onnx-asr",
    quantization="int8",
    revision="0bbb45a3365852604aef28b538a8f066f4ccaa85",
    repo="istupakov/parakeet-tdt-0.6b-v2-onnx",
    license="CC-BY-4.0",
    attribution="NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0), ONNX export by istupakov",
    files=(
        ModelFile(
            name="config.json",
            bytes=97,
            sha256="666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466",
        ),
        ModelFile(
            name="vocab.txt",
            bytes=9384,
            sha256="ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
        ),
        ModelFile(
            name="nemo128.onnx",
            bytes=139764,
            sha256="a9fde1486ebfcc08f328d75ad4610c67835fea58c73ba57e3209a6f6cf019e9f",
        ),
        ModelFile(
            name="encoder-model.int8.onnx",
            bytes=652184014,
            sha256="3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155",
        ),
        ModelFile(
            name="decoder_joint-model.int8.onnx",
            bytes=8998286,
            sha256="a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3",
        ),
    ),
)

ASSETS: dict[str, ModelAsset] = {
    PARAKEET_TDT_0_6B_V2.id: PARAKEET_TDT_0_6B_V2,
}

DEFAULT_STT_ASSET_ID = PARAKEET_TDT_0_6B_V2.id


def get_asset(asset_id: str) -> ModelAsset:
    try:
        return ASSETS[asset_id]
    except KeyError as exc:
        raise KeyError(f"Unknown model asset: {asset_id!r}") from exc


def stt_assets() -> list[ModelAsset]:
    return [asset for asset in ASSETS.values() if asset.engine == "onnx-asr"]
