"""Provider adapters (D9): OpenAI-compatible, Ollama, and ACP agents."""

from keyboard_killer.models.providers.base import (
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderState,
    ProviderStatus,
    Role,
    RoleRequirement,
    ProviderBinding,
    check_provider_for_role,
)
from keyboard_killer.models.providers.ollama import OllamaProvider
from keyboard_killer.models.providers.openai_compat import OpenAICompatProvider

__all__ = [
    "OllamaProvider",
    "OpenAICompatProvider",
    "Provider",
    "ProviderBinding",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderState",
    "ProviderStatus",
    "Role",
    "RoleRequirement",
    "check_provider_for_role",
]
