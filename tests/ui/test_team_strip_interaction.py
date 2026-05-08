"""Tests for the clickable + tooltip-bearing enemy portraits in
LiveCompanion's SummaryRow (v1.10.105).

Restores click-to-cycle-role-override that went dormant when EnemyRow
widgets were retired in the LiveCompanion redesign, and adds a per-
enemy counter-play tooltip via ``view.enemy_counter_tips``.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from champ_assistant.ui.live_companion_view import (
    _PortraitSlot,
    _TeamStrip,
    _role_abbrev,
)


def test_role_abbreviation_maps_lcu_tokens_to_three_letter_badges() -> None:
    """v1.10.115: full role names like SUPPORT clipped to UPPOR at the
    portrait width. Abbreviation map keeps the badge under the
    _PORTRAIT_PX cap."""
    assert _role_abbrev("TOP") == "TOP"
    assert _role_abbrev("JUNGLE") == "JNG"
    assert _role_abbrev("MIDDLE") == "MID"
    assert _role_abbrev("MID") == "MID"
    assert _role_abbrev("BOTTOM") == "BOT"
    assert _role_abbrev("BOT") == "BOT"
    # Both the LCU token (UTILITY) and the user-facing token (SUPPORT)
    # collapse to the same SUP badge.
    assert _role_abbrev("SUPPORT") == "SUP"
    assert _role_abbrev("UTILITY") == "SUP"
    # Empty / unknown — degrade to the first 3 chars uppercased rather
    # than raising. Empty stays empty so no badge renders.
    assert _role_abbrev("") == ""
    assert _role_abbrev("planet") == "PLA"


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _click(widget) -> None:  # type: ignore[no-untyped-def]
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


# ----- _PortraitSlot --------------------------------------------------

def test_portrait_slot_emits_click_with_index(qt_app) -> None:
    slot = _PortraitSlot(slot_index=3)
    received: list[int] = []
    slot.clicked.connect(received.append)
    _click(slot)
    assert received == [3]


# ----- _TeamStrip click → cell_id mapping ----------------------------

def test_team_strip_click_emits_correct_cell_id(qt_app) -> None:
    """Click slot 0 → the strip should emit the cell_id stored at
    cell_ids[0], not the raw slot index."""
    strip = _TeamStrip("Enemy Team")
    received: list[int] = []
    strip.slot_clicked.connect(received.append)

    strip.set_team(
        keys=["Garen", "Lee Sin", "Ahri", "Caitlyn", "Thresh"],
        icon_lookup=lambda key: None,
        cell_ids=[5, 6, 7, 8, 9],
    )
    _click(strip._slots[2])  # third slot
    assert received == [7]


def test_team_strip_click_without_cell_ids_does_not_emit(qt_app) -> None:
    """Ally-strip configuration: no cell_ids passed, click is a noop
    (cell_id default -1 fails the >= 0 guard)."""
    strip = _TeamStrip("Your Team")
    received: list[int] = []
    strip.slot_clicked.connect(received.append)

    strip.set_team(
        keys=["Garen", "Lee Sin", "Ahri", "Caitlyn", "Thresh"],
        icon_lookup=lambda key: None,
    )
    _click(strip._slots[0])
    assert received == []


def test_team_strip_tooltip_renders_per_slot(qt_app) -> None:
    """``set_team(... tooltips=...)`` populates QLabel.toolTip on each
    portrait. Empty entries clear the tooltip."""
    strip = _TeamStrip("Enemy Team")
    strip.set_team(
        keys=["Garen", "Vayne", "Ahri", "", ""],
        icon_lookup=lambda key: None,
        cell_ids=[5, 6, 7, 8, 9],
        tooltips=[
            "Tank tip",
            "Hyper-carry tip",
            "Mage tip",
            "",
            "",
        ],
    )
    assert strip._slots[0].toolTip() == "Tank tip"
    assert strip._slots[1].toolTip() == "Hyper-carry tip"
    assert strip._slots[2].toolTip() == "Mage tip"
    assert strip._slots[3].toolTip() == ""
    assert strip._slots[4].toolTip() == ""


def test_team_strip_role_label_shows_resolved_role(qt_app) -> None:
    """v1.10.106: clicking an enemy portrait silently cycled the
    override before — no visual feedback. Role label below the
    portrait now shows the auto-detected lane and flips to accent
    color when the user manually overrides."""
    from champ_assistant.ui import styles

    strip = _TeamStrip("Enemy Team")
    strip.set_team(
        keys=["Garen", "Lee Sin", "Ahri", "Caitlyn", "Thresh"],
        icon_lookup=lambda key: None,
        cell_ids=[5, 6, 7, 8, 9],
        roles=["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"],
        overridden_indices={2},  # MID was manually overridden
    )

    # All five labels render the 3-letter abbreviation that fits the
    # portrait width (v1.10.115 — full names like "SUPPORT" clipped
    # to "UPPOR" at the previous fixed width).
    assert [lbl.text() for lbl in strip._role_labels] == [
        "TOP", "JNG", "MID", "BOT", "SUP",
    ]
    # Slot 2 (overridden) renders in accent; others muted.
    assert styles.ACCENT in strip._role_labels[2].styleSheet()
    assert styles.TEXT_MUTED in strip._role_labels[0].styleSheet()


def test_team_strip_empty_role_clears_label(qt_app) -> None:
    """When no role is assigned (e.g. PLANNING phase before role
    inference) the label stays blank rather than showing a stale
    value from a prior tick."""
    strip = _TeamStrip("Enemy Team")
    # First populate with roles. (Abbreviated to TOP — same as input.)
    strip.set_team(
        keys=["Garen"], icon_lookup=lambda k: None,
        cell_ids=[5], roles=["TOP"],
    )
    assert strip._role_labels[0].text() == "TOP"
    # Then clear.
    strip.set_team(
        keys=["Garen"], icon_lookup=lambda k: None,
        cell_ids=[5], roles=[""],
    )
    assert strip._role_labels[0].text() == ""


def test_enable_clicks_sets_pointing_hand_cursor(qt_app) -> None:
    """Visual hint: enemy strip portraits show the "this is clickable"
    cursor; ally strip stays default. Same UX convention as PicksColumn /
    BansColumn rows."""
    strip = _TeamStrip("Enemy Team")
    strip.enable_clicks()
    for slot in strip._slots:
        assert slot.cursor().shape() == Qt.CursorShape.PointingHandCursor

    ally_strip = _TeamStrip("Your Team")
    # No enable_clicks() call — cursor stays at the default.
    assert ally_strip._slots[0].cursor().shape() != Qt.CursorShape.PointingHandCursor
