"""Tests for the persisted overlay window state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant import overlay_config


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "overlay.json"
    monkeypatch.setattr(overlay_config, "config_path", lambda: cfg)
    return cfg


def test_load_returns_defaults_when_missing(tmp_config: Path) -> None:
    state = overlay_config.load()
    assert state.x is None
    assert state.width == 320
    assert state.anchor == "right"
    assert state.always_on_top is True


def test_save_then_load_roundtrip(tmp_config: Path) -> None:
    state = overlay_config.OverlayState(
        x=1500, y=120, width=360, height=820,
        anchor="left", always_on_top=False,
        frameless=True, collapsed=True,
    )
    overlay_config.save(state)
    reloaded = overlay_config.load()
    assert reloaded == state


def test_load_ignores_corrupt_json(tmp_config: Path) -> None:
    tmp_config.write_text("{ not json")
    state = overlay_config.load()
    assert state.x is None  # falls back to defaults


def test_load_ignores_unknown_keys(tmp_config: Path) -> None:
    tmp_config.write_text(json.dumps({"x": 100, "weird": True}))
    state = overlay_config.load()
    assert state.x == 100
