"""OpenAI-compatible provider presets (one client, many endpoints)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PRESETS_PATH = Path(__file__).with_name("presets.json")


@dataclass(frozen=True)
class OpenAIPreset:
    id: str
    name: str
    base_url: str
    api_key_env: str
    default_model: str
    docs: str
    local: bool
    latency_class: str
    image_input: bool

    @property
    def requires_key(self) -> bool:
        return not self.local and bool(self.api_key_env)


@lru_cache(maxsize=1)
def load_presets() -> tuple[OpenAIPreset, ...]:
    payload = json.loads(PRESETS_PATH.read_text())
    return tuple(OpenAIPreset(**item) for item in payload)


def get_preset(preset_id: str) -> OpenAIPreset:
    for preset in load_presets():
        if preset.id == preset_id:
            return preset
    raise KeyError(f"Unknown OpenAI-compatible preset: {preset_id!r}")
