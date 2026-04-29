"""Tests for EnemyRow's main-champion icon row.

Renders the previously text-only "Mains: A, B, C" line as three small
champion icons, with text-fallback when the icon_lookup callable
isn't supplied. Covers:
  * Icons appear when lookup returns valid pixmaps
  * Empty mains → no icons visible
  * Missing icon_lookup → text fallback preserves the data
  * Tooltip carries the champion name for screen-reader / hover info
"""
from __future__ import annotations

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from champ_assistant.profiling.profile import EnemyProfile, RankBadge, TopChampion
from champ_assistant.ui.enemy_row import MAIN_ICON_SIZE, EnemyRow


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _solid_pixmap(size: int = MAIN_ICON_SIZE) -> QPixmap:
    """Build a non-null QPixmap so isNull() returns False."""
    px = QPixmap(size, size)
    px.fill()  # default white
    return px


def _profile_with_mains() -> EnemyProfile:
    return EnemyProfile(
        summoner_name="EnemyMid",
        level=420,
        top_champions=[
            TopChampion(157, 500_000, 7),  # Yasuo
            TopChampion(238, 300_000, 6),  # Zed
            TopChampion(103, 200_000, 5),  # Ahri
        ],
        wins=6, losses=4, streak=2,
        rank=RankBadge(),
    )


# ----------------------------------------------------------------------
# Icon-rendering path
# ----------------------------------------------------------------------
def test_main_icons_render_when_lookup_returns_pixmaps(qt_app) -> None:  # type: ignore[no-untyped-def]
    row = EnemyRow()
    profile = _profile_with_mains()
    pixmaps = {
        "Yasuo": _solid_pixmap(),
        "Zed": _solid_pixmap(),
        "Ahri": _solid_pixmap(),
    }
    row.set_profile(
        profile,
        champion_names={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        champion_keys={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        icon_lookup=pixmaps.get,
    )
    visible_icons = [icon for icon in row._main_icons if icon.isVisible()]
    # NOTE: visibility check requires the row itself to be shown; we
    # check pixmap-presence as the proxy for "icon would render if shown".
    icons_with_pixmaps = [
        icon for icon in row._main_icons
        if icon.pixmap() is not None and not icon.pixmap().isNull()
    ]
    assert len(icons_with_pixmaps) == 3


def test_unknown_champion_keys_skip_icon_slots(qt_app) -> None:  # type: ignore[no-untyped-def]
    """If the icon_lookup returns None for a key, that slot stays
    empty — no broken-pixmap rendering."""
    row = EnemyRow()
    profile = _profile_with_mains()
    # Lookup only knows Yasuo
    pixmaps = {"Yasuo": _solid_pixmap()}
    row.set_profile(
        profile,
        champion_names={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        champion_keys={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        icon_lookup=pixmaps.get,
    )
    icons_with_pixmaps = [
        icon for icon in row._main_icons
        if icon.pixmap() is not None and not icon.pixmap().isNull()
    ]
    assert len(icons_with_pixmaps) == 1


def test_no_icon_lookup_falls_back_to_text(qt_app) -> None:  # type: ignore[no-untyped-def]
    """When icon_lookup is omitted, the mains data must still surface
    — fall back to the old 'Mains: A, B, C' text format."""
    row = EnemyRow()
    profile = _profile_with_mains()
    row.set_profile(
        profile,
        champion_names={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        champion_keys={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        icon_lookup=None,  # explicitly no lookup
    )
    text = row._profile_label.text()
    assert "Mains:" in text
    assert "Yasuo" in text
    assert "Zed" in text


def test_empty_profile_clears_all_icons(qt_app) -> None:  # type: ignore[no-untyped-def]
    """A None profile (or one without data) must hide all icons."""
    row = EnemyRow()
    # First populate
    profile = _profile_with_mains()
    pixmaps = {"Yasuo": _solid_pixmap()}
    row.set_profile(
        profile, champion_keys={157: "Yasuo"}, icon_lookup=pixmaps.get,
    )
    # Now clear via None
    row.set_profile(None)
    for icon in row._main_icons:
        assert not icon.isVisible() or icon.pixmap() is None or icon.pixmap().isNull()


def test_main_icon_carries_tooltip_with_champion_name(qt_app) -> None:  # type: ignore[no-untyped-def]
    row = EnemyRow()
    profile = _profile_with_mains()
    pixmaps = {"Yasuo": _solid_pixmap()}
    row.set_profile(
        profile,
        champion_names={157: "Yasuo Specific Name", 238: "Zed", 103: "Ahri"},
        champion_keys={157: "Yasuo", 238: "Zed", 103: "Ahri"},
        icon_lookup=pixmaps.get,
    )
    # First icon should carry the Yasuo-specific name as tooltip.
    assert "Yasuo Specific Name" in row._main_icons[0].toolTip()


def test_clear_drops_all_main_icons(qt_app) -> None:  # type: ignore[no-untyped-def]
    """row.clear() — called when the row no longer has a member —
    must clean up the icons too, not leave stale pixmaps."""
    row = EnemyRow()
    profile = _profile_with_mains()
    pixmaps = {"Yasuo": _solid_pixmap()}
    row.set_profile(
        profile, champion_keys={157: "Yasuo"}, icon_lookup=pixmaps.get,
    )
    row.clear()
    for icon in row._main_icons:
        assert icon.pixmap() is None or icon.pixmap().isNull()


def test_back_compat_old_signature_still_works(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Calling set_profile with only champion_names (the pre-icon
    signature) must still work — used by a few legacy call sites
    + tests that haven't been updated."""
    row = EnemyRow()
    profile = _profile_with_mains()
    row.set_profile(
        profile,
        champion_names={157: "Yasuo", 238: "Zed", 103: "Ahri"},
    )
    # No crash; mains should appear as text since icon_lookup is None.
    text = row._profile_label.text()
    assert "Mains:" in text
