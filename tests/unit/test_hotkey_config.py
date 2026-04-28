"""Tests for hotkey config persistence + combo string parsing."""
from __future__ import annotations

from pathlib import Path

import pytest

from champ_assistant import hotkey_config
from champ_assistant.hotkey_config import (
    DEFAULT_HOTKEYS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    HotkeyConfig,
    format_combo,
    is_valid_combo,
    parse_combo,
)


# --------------------------------------------------------------------------
# parse_combo
# --------------------------------------------------------------------------
def test_parse_simple_letter_combo() -> None:
    result = parse_combo("Ctrl+Alt+H")
    assert result == (MOD_CONTROL | MOD_ALT, ord("H"))


def test_parse_function_key() -> None:
    result = parse_combo("Ctrl+Shift+F1")
    assert result == (MOD_CONTROL | MOD_SHIFT, 0x70)


def test_parse_function_key_24() -> None:
    """Boundary check — Win32 supports up to F24."""
    result = parse_combo("Ctrl+F24")
    assert result == (MOD_CONTROL, 0x87)


def test_parse_digit_combo() -> None:
    result = parse_combo("Alt+5")
    assert result == (MOD_ALT, ord("5"))


def test_parse_case_insensitive() -> None:
    a = parse_combo("ctrl+alt+h")
    b = parse_combo("CTRL+ALT+H")
    c = parse_combo("Ctrl+Alt+H")
    assert a == b == c


def test_parse_modifier_only_rejected() -> None:
    """No key body — invalid for our spec ('Disallowed: Single modifier
    keys only')."""
    assert parse_combo("Ctrl") is None
    assert parse_combo("Ctrl+Alt") is None
    assert parse_combo("Ctrl+Shift+Alt") is None


def test_parse_unknown_token_rejected() -> None:
    assert parse_combo("Ctrl+Alt+SomeWeirdKey") is None
    assert parse_combo("Ctrl+F99") is None  # F25+ not supported by Win32


def test_parse_empty_string() -> None:
    assert parse_combo("") is None
    assert parse_combo("   ") is None


def test_parse_non_string() -> None:
    assert parse_combo(None) is None  # type: ignore[arg-type]
    assert parse_combo(42) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# format_combo + roundtrip
# --------------------------------------------------------------------------
def test_format_orders_modifiers_canonically() -> None:
    """Even if parsed in reverse order, format always emits Ctrl+Alt+Shift+Win."""
    mods, vk = parse_combo("Shift+Alt+Ctrl+H")  # type: ignore[misc]
    assert format_combo(mods, vk) == "Ctrl+Alt+Shift+H"


def test_roundtrip_letter() -> None:
    parsed = parse_combo("Ctrl+Alt+H")
    assert parsed is not None
    assert format_combo(*parsed) == "Ctrl+Alt+H"


def test_roundtrip_function_key() -> None:
    parsed = parse_combo("Ctrl+F12")
    assert parsed is not None
    assert format_combo(*parsed) == "Ctrl+F12"


def test_is_valid_helper() -> None:
    assert is_valid_combo("Ctrl+Alt+H") is True
    assert is_valid_combo("Ctrl") is False


# --------------------------------------------------------------------------
# load / save
# --------------------------------------------------------------------------
@pytest.fixture
def tmp_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "config.json"
    monkeypatch.setattr(hotkey_config, "config_path", lambda: p)
    return p


def test_load_returns_defaults_when_missing(tmp_cfg: Path) -> None:
    cfg = hotkey_config.load()
    assert cfg.hotkeys == DEFAULT_HOTKEYS


def test_load_returns_defaults_on_corrupt_json(tmp_cfg: Path) -> None:
    tmp_cfg.write_text("{ not json")
    cfg = hotkey_config.load()
    assert cfg.hotkeys == DEFAULT_HOTKEYS


def test_load_keeps_defaults_for_invalid_combos(tmp_cfg: Path) -> None:
    """An invalid combo in the config should fall back to default for
    that action without contaminating other actions."""
    tmp_cfg.write_text(
        '{"hotkeys": {"toggle_overlay": "Ctrl+Alt+", "toggle_lock": "Ctrl+Alt+M"}}'
    )
    cfg = hotkey_config.load()
    assert cfg.hotkeys["toggle_overlay"] == DEFAULT_HOTKEYS["toggle_overlay"]
    assert cfg.hotkeys["toggle_lock"] == "Ctrl+Alt+M"


def test_save_then_load_roundtrip(tmp_cfg: Path) -> None:
    cfg = HotkeyConfig.defaults()
    cfg.hotkeys["toggle_overlay"] = "Ctrl+Shift+F1"
    hotkey_config.save(cfg)
    loaded = hotkey_config.load()
    assert loaded.hotkeys["toggle_overlay"] == "Ctrl+Shift+F1"
    assert loaded.hotkeys["toggle_lock"] == DEFAULT_HOTKEYS["toggle_lock"]


def test_save_strips_invalid_entries(tmp_cfg: Path) -> None:
    cfg = HotkeyConfig.defaults()
    cfg.hotkeys["toggle_overlay"] = "garbage"
    hotkey_config.save(cfg)
    # Reload — invalid entry was dropped, default restored.
    loaded = hotkey_config.load()
    assert loaded.hotkeys["toggle_overlay"] == DEFAULT_HOTKEYS["toggle_overlay"]


def test_load_ignores_unknown_action_keys(tmp_cfg: Path) -> None:
    """Forward-compat: a future schema with extra actions shouldn't break
    today's loader."""
    tmp_cfg.write_text(
        '{"hotkeys": {"toggle_overlay": "Ctrl+Alt+H", "future_action": "Ctrl+Alt+X"}}'
    )
    cfg = hotkey_config.load()
    assert cfg.hotkeys["toggle_overlay"] == "Ctrl+Alt+H"
    assert "future_action" not in cfg.hotkeys
