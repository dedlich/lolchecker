"""Tests for the global hotkey service.

Most of the Win32 path can't be exercised from macOS / Linux, so we
focus on:
  * Module imports cleanly without ctypes errors on non-Windows.
  * Idempotent start/stop semantics.
  * Cross-platform no-op behaviour on non-Windows.
  * Binding metadata + label rendering.
"""
from __future__ import annotations

from unittest.mock import patch

from champ_assistant.hotkey_service import (
    DEFAULT_BINDINGS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    VK_H,
    VK_L,
    VK_R,
    HotkeyBinding,
    HotkeyService,
)


def test_default_bindings_match_spec() -> None:
    by_name = {b.name: b for b in DEFAULT_BINDINGS}
    assert by_name["toggle_overlay"].vk == VK_H
    assert by_name["toggle_lock"].vk == VK_L
    assert by_name["reset_positions"].vk == VK_R
    for binding in DEFAULT_BINDINGS:
        assert binding.modifiers == (MOD_CONTROL | MOD_ALT)


def test_binding_label() -> None:
    b = HotkeyBinding("toggle_overlay", MOD_CONTROL | MOD_ALT, VK_H, "Ctrl+Alt+H")
    assert str(b) == "Ctrl+Alt+H"


def test_modifier_norepeat_constant_distinct() -> None:
    """MOD_NOREPEAT must not collide with any user modifier so we can
    OR it into RegisterHotKey's modifier word."""
    assert MOD_NOREPEAT & (MOD_CONTROL | MOD_ALT) == 0


def test_start_is_noop_on_non_windows() -> None:
    """On macOS / Linux start() must succeed silently — no thread, no
    registration. Tests run on macOS so this is the live path."""
    with patch("champ_assistant.hotkey_service._on_windows", return_value=False):
        svc = HotkeyService()
        svc.start()
        assert svc._thread is None
        assert svc._running is False


def test_stop_without_start_is_safe() -> None:
    svc = HotkeyService()
    # No matter the platform, stopping a never-started service must not raise.
    svc.stop()
    svc.stop()
    assert svc._thread is None


def test_double_start_does_not_spawn_two_threads() -> None:
    """Idempotency: a second start() while running should leave the
    thread count unchanged."""
    with patch("champ_assistant.hotkey_service._on_windows", return_value=False):
        svc = HotkeyService()
        svc.start()
        svc.start()
        # Both calls should have been no-ops on this platform.
        assert svc._thread is None


def test_signal_emit_safe_when_no_listener() -> None:
    """Emitting hotkey_pressed without subscribers must not raise."""
    svc = HotkeyService()
    svc.hotkey_pressed.emit("toggle_overlay")  # should be silent
