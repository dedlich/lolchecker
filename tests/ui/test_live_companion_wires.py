"""Wire smoke tests for LiveCompanion (v1.10.84).

Confirms ban / pick row clicks bubble up through the new column
widgets → LiveCompanionView → MainOverlay so the LCU dispatch in boot
actually receives the champion key. Catches regressions like a
``pyqtSignal.emit`` connection getting dropped on a future refactor.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from champ_assistant.advisor.ban_suggestions import BanSuggestion
from champ_assistant.advisor.picks import PickSuggestion
from champ_assistant.ui import styles
from champ_assistant.ui.live_companion_sections.bans_column import (
    BansColumn,
    _ClickableRow as _BanClickableRow,
)
from champ_assistant.ui.live_companion_sections.picks_column import (
    PicksColumn,
    _ClickableRow as _PickClickableRow,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _press(widget) -> None:  # type: ignore[no-untyped-def]
    """Simulate a left-button press on ``widget`` so mousePressEvent
    fires the click handler."""
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


def test_bans_column_emits_ban_hover_requested(qt_app) -> None:  # type: ignore[no-untyped-def]
    """A click on a ban row → BansColumn.ban_hover_requested(key)."""
    column = BansColumn()
    received: list[str] = []
    column.ban_hover_requested.connect(received.append)

    row = column._row(
        BanSuggestion(champion_key="Yone", score=8.0, reasons=[]),
        icon_lookup=lambda key: None,
    )
    _press(row)

    assert received == ["Yone"]


def test_picks_column_emits_pick_hover_requested(qt_app) -> None:  # type: ignore[no-untyped-def]
    """A click on a pick row → PicksColumn.pick_hover_requested(key)."""
    column = PicksColumn()
    received: list[str] = []
    column.pick_hover_requested.connect(received.append)

    row = column._row(
        PickSuggestion(champion_key="Ahri", score=7.5, tier="A", reasons=[]),
        icon_lookup=lambda key: None,
        accent=styles.SUCCESS,
        build_reasons=[],
    )
    _press(row)

    assert received == ["Ahri"]


def test_picks_column_renders_build_reasons(qt_app) -> None:  # type: ignore[no-untyped-def]
    """When ``view.suggestion_build_reasons`` carries adaptation reasons
    for a pick (e.g. "vs AP-heavy → Mercury's Treads"), the row must
    surface them — otherwise the build-adapter signal is invisible to
    the player. v1.10.91 wire-up: PicksColumn used to ignore this
    field entirely (PickCard rendered it, but the LiveCompanion redesign
    replaced PickCard with a minimal row that dropped the reasons line).
    """
    from PyQt6.QtWidgets import QLabel

    column = PicksColumn()
    row = column._row(
        PickSuggestion(champion_key="Ahri", score=7.5, tier="A", reasons=[]),
        icon_lookup=lambda key: None,
        accent=styles.SUCCESS,
        build_reasons=["vs AP-heavy → Mercury's Treads", "anti-burst MR boots"],
    )
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    matched = [t for t in texts if "Mercury" in t]
    assert matched, f"build-reasons label missing — got texts {texts}"


def test_picks_column_no_reasons_when_empty(qt_app) -> None:  # type: ignore[no-untyped-def]
    """No reasons in the dict → no italic line. Keeps the row compact
    when the build adapter didn't change anything."""
    from PyQt6.QtWidgets import QLabel

    column = PicksColumn()
    row = column._row(
        PickSuggestion(champion_key="Ahri", score=7.5, tier="A", reasons=[]),
        icon_lookup=lambda key: None,
        accent=styles.SUCCESS,
        build_reasons=[],
    )
    labels = row.findChildren(QLabel)
    texts = [lbl.text() for lbl in labels]
    # Only icon (empty), name, score — no build-reason line.
    assert not any("⚙" in t for t in texts), f"unexpected reasons label: {texts}"


def test_clickable_rows_use_pointing_hand_cursor(qt_app) -> None:  # type: ignore[no-untyped-def]
    """The cursor change is the only visual hint the rows are clickable
    — without it users won't discover the click-to-lock surface."""
    ban_row = _BanClickableRow("Yone", on_click=lambda _key: None)
    pick_row = _PickClickableRow("Ahri", on_click=lambda _key: None)
    assert ban_row.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert pick_row.cursor().shape() == Qt.CursorShape.PointingHandCursor
