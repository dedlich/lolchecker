"""Cross-platform secrets storage backed by ``keyring``.

Keys we persist:
  - Riot API key (developer or production)
  - Riot region (EUW, NA, KR, ...)
  - Groq API key (live counters; previously env-only)

``keyring`` uses the OS credential manager (Windows Credential Manager,
macOS Keychain, GNOME secret-tool). Falls back to a plaintext file in
``~/.local/share/python_keyring/`` only if no real backend is available —
acceptable for our threat model since it's the same machine the API key
was already typed on.

Env-var fallback: any value found in ``RIOT_API_KEY`` / ``GROQ_API_KEY``
takes precedence so power users / CI runs don't need a saved keyring.
"""
from __future__ import annotations

import logging
import os
from typing import Final

import keyring

logger = logging.getLogger(__name__)

SERVICE: Final[str] = "champ-assistant"

KEY_RIOT_API: Final[str] = "riot_api_key"
KEY_RIOT_REGION: Final[str] = "riot_region"
KEY_GROQ_API: Final[str] = "groq_api_key"
KEY_LLM_API: Final[str] = "llm_api_key"
KEY_LLM_PROVIDER: Final[str] = "llm_provider"

DEFAULT_REGION: Final[str] = "EUW"
DEFAULT_LLM_PROVIDER: Final[str] = "openrouter"


def get(key: str, *, env_fallback: str | None = None, default: str = "") -> str:
    """Return the stored value, env override, or default."""
    if env_fallback:
        env = os.environ.get(env_fallback)
        if env:
            return env
    try:
        value = keyring.get_password(SERVICE, key)
    except Exception as exc:  # noqa: BLE001 — keyring backends fail in many ways
        logger.info("keyring_read_failed key=%s: %s", key, exc)
        return default
    return value or default


def set_(key: str, value: str) -> None:
    """Persist a value, or delete if ``value`` is empty."""
    try:
        if value:
            keyring.set_password(SERVICE, key, value)
        else:
            try:
                keyring.delete_password(SERVICE, key)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("keyring_write_failed key=%s: %s", key, exc)


# Convenience accessors so call sites don't need to know the key constants.

def riot_api_key() -> str:
    return get(KEY_RIOT_API, env_fallback="RIOT_API_KEY")


def riot_region() -> str:
    return get(KEY_RIOT_REGION, default=DEFAULT_REGION)


def groq_api_key() -> str:
    return get(KEY_GROQ_API, env_fallback="GROQ_API_KEY")


def llm_provider() -> str:
    """Selected LLM provider for live counter lookups (openrouter/groq/gemini)."""
    return get(KEY_LLM_PROVIDER, default=DEFAULT_LLM_PROVIDER)


def llm_api_key() -> str:
    """Generic LLM key — falls back to legacy GROQ_API_KEY for back-compat."""
    return get(KEY_LLM_API) or groq_api_key()


def set_riot_api_key(value: str) -> None:
    set_(KEY_RIOT_API, value)


def set_riot_region(value: str) -> None:
    set_(KEY_RIOT_REGION, value or DEFAULT_REGION)


def set_groq_api_key(value: str) -> None:
    set_(KEY_GROQ_API, value)


def set_llm_provider(value: str) -> None:
    set_(KEY_LLM_PROVIDER, value or DEFAULT_LLM_PROVIDER)


def set_llm_api_key(value: str) -> None:
    set_(KEY_LLM_API, value)
