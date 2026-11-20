"""OS keychain storage for provider credentials (never in settings or logs)."""

from __future__ import annotations

import importlib.util
import logging
import os

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "keyless"


def keyring_available() -> bool:
    return importlib.util.find_spec("keyring") is not None


def _keyring():
    import keyring

    return keyring


def get_secret(provider_id: str) -> str | None:
    if not keyring_available():
        return None
    try:
        return _keyring().get_password(KEYRING_SERVICE, provider_id) or None
    except Exception as exc:  # keyring backends raise backend-specific errors
        logger.warning("Could not read keychain entry for %s: %s", provider_id, exc)
        return None


def set_secret(provider_id: str, secret: str) -> None:
    if not secret:
        delete_secret(provider_id)
        return
    if not keyring_available():
        raise RuntimeError("The OS keychain is unavailable; install the keyring backend.")
    _keyring().set_password(KEYRING_SERVICE, provider_id, secret)


def delete_secret(provider_id: str) -> None:
    if not keyring_available():
        return
    try:
        _keyring().delete_password(KEYRING_SERVICE, provider_id)
    except Exception:
        return


def resolve_api_key(provider_id: str, env_var: str = "") -> str | None:
    """Keychain first, then the documented environment variable fallback."""
    secret = get_secret(provider_id)
    if secret:
        return secret
    if env_var:
        value = os.environ.get(env_var)
        if value:
            return value
    return None
