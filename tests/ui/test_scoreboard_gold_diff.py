"""Tests for the scoreboard-scoped gold-diff overlay — visibility
gating + value rendering."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from champ_assistant.state_store import StateStore
from champ_assistant.ui.scoreboard_overlay import (
    GoldDifferencePanel,
    ScoreboardOverlayController,
    _color_for_delta,
    _format_gold_delta,
)
from champ_assistant.ui import styles


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _snap(ally_value: int, enemy_value: int, *, active_team: str = "ORDER"):
    """Build a minimal mock LcdaSnapshot."""
    return SimpleNamespace(
        ally_aggregate=SimpleNamespace(items_value=ally_value),
        enemy_aggregate=SimpleNamespace(items_value=enemy_value),
        active_team=active_team,
        enemy_team="CHAOS" if active_team == "ORDER" else "ORDER",
        allies=[], enemies=[],
        game_time=600.0,
    )


# ----------------------------------------------------------------------
# Pure formatter / color helpers
# ----------------------------------------------------------------------
def test_format_positive_has_explicit_plus_sign() -> None:
    assert _format_gold_delta(1250) == "+1250"


def test_format_negative_has_minus_sign() -> None:
    assert _format_gold_delta(-300) == "-300"


def test_format_zero_has_no_sign() -> None:
    assert _format_gold_delta(0) == "0"


def test_color_for_delta_uses_design_tokens() -> None:
    assert _color_for_delta(1) == styles.SUCCESS
    assert _color_for_delta(-1) == styles.DANGER
    assert _color_for_delta(0) == styles.TEXT_MUTED


# ----------------------------------------------------------------------
# Visibility gating — controller hides/shows based on state.scoreboard_visible
# ----------------------------------------------------------------------
def test_panel_hidden_by_default(qt_app) -> None:  # type: ignore[no-untyped-def]
    panel = GoldDifferencePanel()
    assert panel.isVisible() is False


def test_controller_does_not_show_panel_when_scoreboard_hidden(qt_app) -> None:  # type: ignore[no-untyped-def]
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)

    # Default state: scoreboard_visible=False.
    store.update(lcda_snapshot=_snap(15000, 12000))
    assert panel.isVisible() is False
    controller.stop()


def test_controller_shows_panel_when_scoreboard_visible(qt_app) -> None:  # type: ignore[no-untyped-def]
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)

    # Pretending vision flipped the flag.
    store.update(lcda_snapshot=_snap(15000, 12000), scoreboard_visible=True)
    qt_app.processEvents()
    assert panel.isVisible() is True
    controller.stop()


def test_controller_hides_panel_when_scoreboard_flips_off(qt_app) -> None:  # type: ignore[no-untyped-def]
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)

    store.update(lcda_snapshot=_snap(15000, 12000), scoreboard_visible=True)
    qt_app.processEvents()
    assert panel.isVisible() is True

    store.update(scoreboard_visible=False)
    qt_app.processEvents()
    assert panel.isVisible() is False
    controller.stop()


def test_value_updates_only_when_visible(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Snapshot updates while the scoreboard is hidden must NOT
    waste cycles updating the (invisible) panel — but when it
    becomes visible the latest value should be there."""
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)

    # Push snapshot while hidden — panel text shouldn't update.
    initial_text = panel._value_label.text()
    store.update(lcda_snapshot=_snap(15000, 12000))
    qt_app.processEvents()
    assert panel._value_label.text() == initial_text

    # Now flip visible — the controller should refresh from latest.
    store.update(scoreboard_visible=True)
    qt_app.processEvents()
    assert panel._value_label.text() == "+3000"
    controller.stop()


def test_value_format_negative(qt_app) -> None:  # type: ignore[no-untyped-def]
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)
    store.update(lcda_snapshot=_snap(10000, 13000), scoreboard_visible=True)
    qt_app.processEvents()
    assert panel._value_label.text() == "-3000"
    controller.stop()


def test_controller_stop_drops_subscription(qt_app) -> None:  # type: ignore[no-untyped-def]
    """After stop(), state-store updates must NOT call back into the
    controller — covers the lifecycle teardown path."""
    store = StateStore()
    panel = GoldDifferencePanel()
    controller = ScoreboardOverlayController(state_store=store, panel=panel)
    controller.stop()

    # Push a flip — controller's subscription is gone, panel state unchanged.
    store.update(scoreboard_visible=True, lcda_snapshot=_snap(15000, 12000))
    qt_app.processEvents()
    # If the unsub didn't take, the panel would be visible. It isn't.
    assert panel.isVisible() is False
