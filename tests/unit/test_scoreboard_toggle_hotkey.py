"""Tests for the manual toggle_scoreboard hotkey wiring.

Pure data tests — verify the binding exists in the canonical
DEFAULT_BINDINGS / DEFAULT_HOTKEYS so a future re-shuffle of the
hotkey list doesn't silently drop it. The actual ``store.update``
side-effect lives in ``__main__._on_hotkey`` which is integration-
tested via the real app run.
"""
from __future__ import annotations

from champ_assistant import hotkey_config
from champ_assistant.hotkey_service import DEFAULT_BINDINGS


def test_default_hotkeys_includes_toggle_scoreboard() -> None:
    assert "toggle_scoreboard" in hotkey_config.DEFAULT_HOTKEYS


def test_default_combo_is_ctrl_alt_b() -> None:
    """Ctrl+Alt+B is documented as the manual scoreboard-toggle
    combo. Other Ctrl+Alt+X combos already in use: H/L/R/D — B
    avoids collision."""
    assert hotkey_config.DEFAULT_HOTKEYS["toggle_scoreboard"] == "Ctrl+Alt+B"


def test_default_bindings_includes_toggle_scoreboard() -> None:
    """The Win32 binding tuple must mirror DEFAULT_HOTKEYS — a
    binding-without-config or config-without-binding both create
    silent breakage."""
    names = {b.name for b in DEFAULT_BINDINGS}
    assert "toggle_scoreboard" in names


def test_no_collision_with_other_ctrl_alt_combos() -> None:
    """Defensive: every combo string in DEFAULT_HOTKEYS is unique.
    A duplicate would silently override one binding."""
    combos = list(hotkey_config.DEFAULT_HOTKEYS.values())
    assert len(combos) == len(set(combos)), (
        f"duplicate hotkey combo in DEFAULT_HOTKEYS: {combos}"
    )
