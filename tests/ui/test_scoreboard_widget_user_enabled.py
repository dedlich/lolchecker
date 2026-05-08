"""Tests for ScoreboardWidget's user-enabled gate (v1.10.99).

Construct-then-hide refactor: the widget is now constructed
unconditionally at boot so the user can flip Show Scoreboard in
Settings without restarting. The flag routes through ``set_user_enabled``,
which gates ``set_peek_visible`` so the in-game peek driver doesn't
re-summon a user-disabled widget on the next game-start transition.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from champ_assistant.ui.scoreboard_widget import ScoreboardWidget


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def test_set_user_enabled_false_blocks_peek(qt_app) -> None:
    """``set_peek_visible(True)`` must not show the widget when the
    user has disabled the scoreboard. Otherwise the in-game peek
    driver — which fires on every game-start transition regardless
    of user preference — would re-summon a panel the user explicitly
    turned off."""
    w = ScoreboardWidget()
    # Pretend a snapshot landed so the snapshot-required guard passes.
    w._latest_snapshot = object()

    w.set_user_enabled(False)
    w.set_peek_visible(True)
    assert not w.isVisible(), (
        "user disabled the scoreboard — peek driver must not summon it"
    )


def test_set_user_enabled_false_hides_visible_widget(qt_app) -> None:
    """Disabling mid-game (Settings → uncheck → Save) hides the panel
    immediately rather than waiting for the next game-end transition."""
    w = ScoreboardWidget()
    w._latest_snapshot = object()
    w.set_user_enabled(True)
    w.set_peek_visible(True)
    assert w.isVisible()

    w.set_user_enabled(False)
    assert not w.isVisible()


def test_set_user_enabled_re_enable_allows_peek(qt_app) -> None:
    """Re-enabling restores the peek path so the next game-start
    transition shows the panel again."""
    w = ScoreboardWidget()
    w._latest_snapshot = object()

    w.set_user_enabled(False)
    w.set_peek_visible(True)
    assert not w.isVisible()

    w.set_user_enabled(True)
    w.set_peek_visible(True)
    assert w.isVisible()
