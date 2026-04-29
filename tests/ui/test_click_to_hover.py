"""Click-to-hover signal flow on PickCard and BanRow.

Verifies the click-driven LCU hover path at the UI level:
  * PickCard emits ``pick_hover_requested`` on left-click body
  * Apply Build button click does NOT emit pick_hover_requested
  * _BanRow emits ``ban_hover_requested`` on left-click
  * BanPanel bubbles row signals up

The actual LCU PATCH is handled by champ_assistant.lcu.champ_select
and tested separately — this test only proves the UI signal contract.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from champ_assistant.advisor.ban_suggestions import BanSuggestion
from champ_assistant.advisor.picks import PickSuggestion
from champ_assistant.ui.ban_panel import BanPanel, _BanRow
from champ_assistant.ui.pick_card import PickCard


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _press(widget, point: QPoint | None = None) -> None:
    """Synthesize a left-button press at ``point`` (centre by default)."""
    if point is None:
        point = widget.rect().center()
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        point.toPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


def _pick(name: str = "Ahri") -> PickSuggestion:
    return PickSuggestion(
        champion_key=name, score=80.0, tier="A", reasons=["counters X"],
    )


def _ban(name: str = "Yone") -> BanSuggestion:
    return BanSuggestion(champion_key=name, score=90.0, reasons=["meta"])


def test_pick_card_emits_hover_on_body_click(qt_app) -> None:
    card = PickCard(_pick("Ahri"), rank=1)
    received: list[str] = []
    card.pick_hover_requested.connect(received.append)
    _press(card)
    assert received == ["Ahri"]


def test_pick_card_apply_button_does_not_emit_hover(qt_app) -> None:
    """The Apply Build button must not double-fire the hover signal —
    user wanting to push runes/items shouldn't accidentally hover."""
    from champ_assistant.data.models import ChampionBuild
    build = ChampionBuild(
        runes=["Conqueror"], items=["Stridebreaker"], summoners=["Flash"],
    )
    card = PickCard(_pick("Ahri"), build=build, rank=1)
    received_hover: list[str] = []
    received_apply: list[str] = []
    card.pick_hover_requested.connect(received_hover.append)
    card.apply_build_requested.connect(
        lambda key, runes, items: received_apply.append(key),
    )
    # Find the Apply Build button and click() it directly.
    buttons = card.findChildren(QPushButton)
    assert buttons, "expected an Apply Build button"
    buttons[0].click()
    assert received_apply == ["Ahri"]
    assert received_hover == []  # button click did not bubble


def test_ban_row_emits_hover_on_click(qt_app) -> None:
    row = _BanRow(_ban("Yone"), icon=None, rank=1)
    received: list[str] = []
    row.ban_hover_requested.connect(received.append)
    _press(row)
    assert received == ["Yone"]


def test_ban_panel_bubbles_row_clicks(qt_app) -> None:
    panel = BanPanel()
    received: list[str] = []
    panel.ban_hover_requested.connect(received.append)
    panel.update_suggestions(
        [_ban("Yone"), _ban("Aatrox")],
        icon_lookup=lambda key: None,
    )
    # Find the underlying rows and click each.
    rows = panel.findChildren(_BanRow)
    assert len(rows) == 2
    for row in rows:
        _press(row)
    assert received == ["Yone", "Aatrox"]


def test_pick_card_cursor_is_pointing_hand(qt_app) -> None:
    """The cursor change is the only visual hint that the card is
    clickable — without it users won't discover the hover feature."""
    card = PickCard(_pick("Ahri"), rank=1)
    assert card.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_ban_row_cursor_is_pointing_hand(qt_app) -> None:
    row = _BanRow(_ban("Yone"), icon=None, rank=1)
    assert row.cursor().shape() == Qt.CursorShape.PointingHandCursor
