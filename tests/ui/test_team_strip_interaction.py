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

from champ_assistant.ui.live_companion_view import _PortraitSlot, _TeamStrip


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
