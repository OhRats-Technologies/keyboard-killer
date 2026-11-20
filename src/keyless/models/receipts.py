"""Verified-download receipts for app-owned model assets."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from keyless.models.catalog import ModelAsset

RECEIPT_SCHEMA = 1


@dataclass(frozen=True)
class FileReceipt:
    name: str
    bytes: int
    sha256: str


@dataclass
class ModelReceipt:
    asset: str
    revision: str
    engine: str
    quantization: str
    files: dict[str, FileReceipt] = field(default_factory=dict)
    completed_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RECEIPT_SCHEMA,
            "asset": self.asset,
            "revision": self.revision,
            "engine": self.engine,
            "quantization": self.quantization,
            "completed_at": self.completed_at,
            "files": [
                {"name": f.name, "bytes": f.bytes, "sha256": f.sha256}
                for f in sorted(self.files.values(), key=lambda item: item.name)
            ],
        }

    def write_atomic(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        os.replace(partial, path)

    @classmethod
    def read(cls, path: Path) -> ModelReceipt | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("schema") != RECEIPT_SCHEMA:
            return None
        files = {
            item["name"]: FileReceipt(
                name=item["name"], bytes=int(item["bytes"]), sha256=str(item["sha256"])
            )
            for item in payload.get("files", [])
        }
        return cls(
            asset=str(payload.get("asset", "")),
            revision=str(payload.get("revision", "")),
            engine=str(payload.get("engine", "")),
            quantization=str(payload.get("quantization", "")),
            files=files,
            completed_at=str(payload.get("completed_at", "")),
        )

    @classmethod
    def for_asset(
        cls,
        asset: ModelAsset,
        files: list[FileReceipt],
    ) -> ModelReceipt:
        return cls(
            asset=asset.id,
            revision=asset.revision,
            engine=asset.engine,
            quantization=asset.quantization,
            files={f.name: f for f in files},
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
