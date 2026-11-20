"""One OpenAI-compatible client with presets for hosted and local endpoints."""

from __future__ import annotations

import base64
import logging
from typing import Sequence

import httpx

from keyless.models.providers.base import (
    BaseProvider,
    ProviderCapabilities,
    ProviderError,
    ProviderState,
    ProviderStatus,
)
from keyless.models.providers.presets import OpenAIPreset, get_preset
from keyless.models.providers.secrets import resolve_api_key

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(5.0, read=60.0)


def _image_part(data: bytes) -> dict[str, object]:
    mime = "image/jpeg" if data.startswith(b"\xff\xd8") else "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}


class OpenAICompatProvider(BaseProvider):
    """POST {base_url}/chat/completions; presets carry endpoint and key env."""

    def __init__(
        self,
        preset_id: str,
        model: str = "",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: httpx.Timeout | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        preset = get_preset(preset_id)
        self.preset: OpenAIPreset = preset
        self.id = preset.id
        self.display_name = preset.name
        self.model = model or preset.default_model
        self.base_url = (base_url or preset.base_url).rstrip("/")
        self._explicit_key = api_key
        self._timeout = timeout or _TIMEOUT
        self._transport = transport
        self.capabilities = ProviderCapabilities(
            text=True,
            json_mode=True,
            image_input=preset.image_input,
            streaming=False,
            latency_class=preset.latency_class,
            local=preset.local,
        )

    @property
    def api_key(self) -> str | None:
        if self._explicit_key is not None:
            return self._explicit_key or None
        return resolve_api_key(self.id, self.preset.api_key_env)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._timeout,
            headers=self._headers(),
            transport=self._transport,
            follow_redirects=True,
        )

    def status(self) -> ProviderStatus:
        if not self.model:
            return ProviderStatus(
                ProviderState.MODEL_MISSING,
                "Choose a model for this provider.",
                model=self.model,
            )
        if self.preset.requires_key and not self.api_key:
            return ProviderStatus(
                ProviderState.NEEDS_AUTH,
                f"Add an API key (keychain or {self.preset.api_key_env}).",
                model=self.model,
            )
        try:
            with self._client() as client:
                response = client.get(f"{self.base_url}/models")
        except httpx.HTTPError as exc:
            return ProviderStatus(
                ProviderState.UNREACHABLE,
                f"Cannot reach {self.base_url}: {type(exc).__name__}.",
                model=self.model,
            )
        if response.status_code in (401, 403):
            return ProviderStatus(
                ProviderState.NEEDS_AUTH,
                "The endpoint rejected the API key.",
                model=self.model,
            )
        if response.status_code >= 400:
            return ProviderStatus(
                ProviderState.UNREACHABLE,
                f"The endpoint returned HTTP {response.status_code}.",
                model=self.model,
            )
        return ProviderStatus(ProviderState.READY, "Endpoint reachable.", model=self.model)

    def complete(
        self,
        system: str,
        user: str,
        *,
        json: bool = False,
        images: Sequence[bytes] = (),
    ) -> str:
        if images and not self.capabilities.image_input:
            raise ProviderError(f"{self.display_name} does not accept image input.")
        user_content: str | list[dict[str, object]] = user
        if images:
            parts: list[dict[str, object]] = [{"type": "text", "text": user}]
            parts.extend(_image_part(image) for image in images)
            user_content = parts
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        if json:
            payload["response_format"] = {"type": "json_object"}
        try:
            with self._client() as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.display_name} request failed: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise ProviderError(f"{self.display_name} rejected the API key.")
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.display_name} returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.display_name} returned an unexpected response.") from exc
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        return str(content).strip()

    def warm(self) -> None:
        # A cheap status probe is the useful warm-up for HTTP providers.
        self.status()
