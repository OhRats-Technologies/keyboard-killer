"""Provider contract, role requirements, and status types (D9)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence, runtime_checkable


class ProviderState(str, Enum):
    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    NOT_INSTALLED = "not_installed"
    UNREACHABLE = "unreachable"
    MODEL_MISSING = "model_missing"
    ERROR = "error"


class ProviderError(RuntimeError):
    """A provider call failed; the message must not contain credentials."""


class Role(str, Enum):
    TEXT_LLM = "text_llm"
    GAZE_VLM = "gaze_vlm"


@dataclass(frozen=True)
class ProviderCapabilities:
    text: bool = True
    json_mode: bool = False
    image_input: bool = False
    streaming: bool = False
    latency_class: str = "slow"  # fast | slow
    local: bool = False


@dataclass(frozen=True)
class ProviderStatus:
    state: ProviderState
    detail: str = ""
    model: str = ""
    auth_methods: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.state is ProviderState.READY

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "detail": self.detail,
            "model": self.model,
            "auth_methods": list(self.auth_methods),
            **self.extra,
        }


@runtime_checkable
class Provider(Protocol):
    id: str
    display_name: str
    model: str
    capabilities: ProviderCapabilities

    def status(self) -> ProviderStatus: ...

    def complete(
        self,
        system: str,
        user: str,
        *,
        json: bool = False,
        images: Sequence[bytes] = (),
    ) -> str: ...

    def warm(self) -> None: ...

    def close(self) -> None: ...


class BaseProvider(ABC):
    """Shared bookkeeping for provider adapters."""

    id: str = "base"
    display_name: str = "Provider"
    model: str = ""
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def status(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        *,
        json: bool = False,
        images: Sequence[bytes] = (),
    ) -> str:
        raise NotImplementedError

    def warm(self) -> None:
        return None

    def pull(self, tag: str, progress: Any | None = None) -> None:
        raise ProviderError(f"{self.id} does not support pull().")

    def close(self) -> None:
        return None

    def __enter__(self) -> BaseProvider:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@dataclass(frozen=True)
class RoleRequirement:
    image_input: bool = False
    max_latency: str = "slow"  # "fast" or "slow"
    json_mode: bool = False


ROLE_REQUIREMENTS: dict[Role, RoleRequirement] = {
    Role.TEXT_LLM: RoleRequirement(image_input=False, max_latency="slow", json_mode=False),
    Role.GAZE_VLM: RoleRequirement(image_input=True, max_latency="fast", json_mode=False),
}


def check_provider_for_role(
    provider: Provider,
    role: Role,
    *,
    remote_vlm_consent: bool = False,
) -> list[str]:
    """Return reasons the provider cannot serve the role; empty means eligible."""
    requirement = ROLE_REQUIREMENTS[role]
    reasons: list[str] = []
    caps = provider.capabilities
    if not caps.text:
        reasons.append("The provider does not support text completions.")
    if requirement.image_input and not caps.image_input:
        reasons.append("The gaze VLM role needs image input; this provider does not accept images.")
    if requirement.max_latency == "fast" and caps.latency_class != "fast":
        reasons.append("The gaze VLM role needs a fast provider; this one is marked slow.")
    if requirement.json_mode and not caps.json_mode:
        reasons.append("The role needs JSON mode.")
    if role is Role.GAZE_VLM and not caps.local and not remote_vlm_consent:
        reasons.append("Screenshots would leave this device; enable remote VLM consent first.")
    return reasons


@dataclass
class ProviderBinding:
    """A role bound to one provider + model, with the checks the UI must show."""

    role: Role
    provider: Provider
    model: str = ""
    remote_vlm_consent: bool = False

    @property
    def reasons(self) -> list[str]:
        return check_provider_for_role(
            self.provider,
            self.role,
            remote_vlm_consent=self.remote_vlm_consent,
        )

    @property
    def eligible(self) -> bool:
        return not self.reasons
