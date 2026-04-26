"""Unit tests for the resource-root helper in __main__."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from champ_assistant.__main__ import _resource_root


def test_resource_root_in_source_layout() -> None:
    """In a regular source checkout, _resource_root points at the repo root."""
    root = _resource_root()
    assert (root / "src" / "champ_assistant").is_dir()
    assert (root / "data").is_dir()


def test_resource_root_in_frozen_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """When PyInstaller has set sys.frozen + sys._MEIPASS, follow the bundle path."""
    fake_bundle = Path("/tmp/some_pyinstaller_bundle")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_bundle), raising=False)
    assert _resource_root() == fake_bundle


def test_resource_root_frozen_without_meipass(monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.frozen alone (no _MEIPASS) should fall back to the source-layout path."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    root = _resource_root()
    # Source layout still resolves to the repo root with src/ + data/.
    assert (root / "src" / "champ_assistant").is_dir()
