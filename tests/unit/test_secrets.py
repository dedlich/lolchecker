"""Tests for the keyring-backed secrets module."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from champ_assistant import secrets


class _FakeKeyring:
    """In-memory stand-in for the keyring backend."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        try:
            del self.store[(service, key)]
        except KeyError:
            from keyring.errors import PasswordDeleteError
            raise PasswordDeleteError("missing") from None


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    return fake


def test_get_returns_default_when_unset() -> None:
    assert secrets.get("missing") == ""
    assert secrets.get("missing", default="fallback") == "fallback"


def test_set_then_get_roundtrip() -> None:
    secrets.set_("riot_api_key", "RGAPI-deadbeef")
    assert secrets.get("riot_api_key") == "RGAPI-deadbeef"


def test_set_empty_deletes_value() -> None:
    secrets.set_("k", "v")
    secrets.set_("k", "")
    assert secrets.get("k") == ""


def test_env_fallback_overrides_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets.set_("riot_api_key", "stored")
    monkeypatch.setenv("RIOT_API_KEY", "from-env")
    assert secrets.riot_api_key() == "from-env"


def test_region_default_is_euw() -> None:
    assert secrets.riot_region() == "EUW"


def test_region_persists() -> None:
    secrets.set_riot_region("KR")
    assert secrets.riot_region() == "KR"


def test_region_empty_resets_to_default() -> None:
    secrets.set_riot_region("")
    assert secrets.riot_region() == "EUW"


def test_keyring_failure_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def get_password(self, *a, **k):  # type: ignore[no-untyped-def]
            raise RuntimeError("keyring backend down")

    monkeypatch.setattr(secrets, "keyring", Broken())
    assert secrets.get("anything") == ""
