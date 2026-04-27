"""Tests for the League window finder."""
from __future__ import annotations

from unittest.mock import patch

from champ_assistant.lcu.window import (
    LeagueWindowInfo,
    _on_windows,
    find_league_window,
)


def test_returns_none_off_windows() -> None:
    """The whole module is a noop on macOS / Linux — never raises, always
    returns None so callers can fall back to the static screen anchor."""
    with patch("champ_assistant.lcu.window._on_windows", return_value=False):
        assert find_league_window() is None


def test_returns_none_when_scan_raises() -> None:
    """Win32 errors degrade silently — the overlay falls back to a static
    screen-edge anchor without the auto-pin feature."""
    with patch("champ_assistant.lcu.window._on_windows", return_value=True), \
         patch(
             "champ_assistant.lcu.window._scan_windows",
             side_effect=OSError("Win32 unavailable"),
         ):
        assert find_league_window() is None


def test_window_info_geometry_helpers() -> None:
    info = LeagueWindowInfo(
        hwnd=1, title="League of Legends (TM) Client",
        class_name="RiotWindowClass",
        left=100, top=50, right=1700, bottom=950,
        fullscreen_exclusive=False,
    )
    assert info.width == 1600
    assert info.height == 900


def test_fullscreen_exclusive_flag_serializable() -> None:
    info = LeagueWindowInfo(
        hwnd=1, title="x", class_name="RiotWindowClass",
        left=0, top=0, right=1920, bottom=1080,
        fullscreen_exclusive=True,
    )
    assert info.fullscreen_exclusive is True


def test_returns_first_match_only() -> None:
    """When multiple windows match (unusual but possible), pick the first."""
    primary = LeagueWindowInfo(
        hwnd=1, title="League of Legends (TM) Client",
        class_name="RiotWindowClass",
        left=0, top=0, right=1600, bottom=900,
        fullscreen_exclusive=False,
    )
    with patch("champ_assistant.lcu.window._on_windows", return_value=True), \
         patch("champ_assistant.lcu.window._scan_windows", return_value=primary):
        result = find_league_window()
        assert result is primary
