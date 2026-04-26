"""pytest-qt smoke tests for the overlay UI."""
from __future__ import annotations

import os

import pytest

# Headless rendering on macOS / CI — must be set before any QApplication import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from champ_assistant.advisor.composition import CompositionGap  # noqa: E402
from champ_assistant.advisor.picks import PickSuggestion  # noqa: E402
from champ_assistant.data.models import (  # noqa: E402
    ChampSelectSession,
    CounterEntry,
    TeamMember,
)
from champ_assistant.ui.overlay import MainOverlay  # noqa: E402
from champ_assistant.ui.pick_card import PickCard  # noqa: E402
from champ_assistant.ui.view_model import SessionView  # noqa: E402


@pytest.fixture
def overlay(qtbot):  # type: ignore[no-untyped-def]
    w = MainOverlay()
    qtbot.addWidget(w)
    return w


def test_window_creates(overlay) -> None:  # type: ignore[no-untyped-def]
    assert overlay.windowTitle() == "Champ Select Assistant"
    assert overlay.size().width() == 420
    assert overlay.size().height() == 640
    assert len(overlay.enemy_rows) == 5


def test_initial_status_is_disconnected(overlay) -> None:  # type: ignore[no-untyped-def]
    assert overlay.status_bar.state == "disconnected"


def test_update_view_sets_connection_state(overlay) -> None:  # type: ignore[no-untyped-def]
    view = SessionView(connection_state="connected")
    overlay.update_view(view)
    assert overlay.status_bar.state == "connected"


def test_update_view_renders_enemies() -> None:
    overlay = MainOverlay()
    session = ChampSelectSession(
        phase="BAN_PICK",
        local_player_cell_id=0,
        my_team=[TeamMember(cell_id=0)],
        their_team=[
            TeamMember(cell_id=5, champion_id=86, assigned_position="TOP"),
            TeamMember(cell_id=6, champion_id=64, assigned_position="JUNGLE"),
        ],
    )
    view = SessionView(
        connection_state="connected",
        session=session,
        enemy_names={86: "Garen", 64: "Lee Sin"},
        enemy_roles={5: "TOP", 6: "JUNGLE"},
        enemy_counters={
            5: [
                CounterEntry(champion="Darius", score=8.0, tier="S"),
                CounterEntry(champion="Vayne", score=6.5, tier="A"),
            ],
        },
    )
    overlay.update_view(view)

    rows = overlay.enemy_rows
    assert "Garen" in rows[0]._champion_label.text()
    assert "TOP" in rows[0]._role_button.text()
    assert "Darius" in rows[0]._counters_label.text()

    assert "Lee Sin" in rows[1]._champion_label.text()
    # Slot 2-4 stay as placeholder
    assert rows[2]._champion_label.text() == EnemyRow_PLACEHOLDER()
    overlay.deleteLater()


def EnemyRow_PLACEHOLDER() -> str:
    from champ_assistant.ui.enemy_row import EnemyRow
    return EnemyRow.PLACEHOLDER


def test_update_view_renders_pick_cards(qtbot) -> None:  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    view = SessionView(
        connection_state="connected",
        suggestions=[
            PickSuggestion(
                champion_key="Darius",
                score=87.5,
                tier="S+",
                reasons=["S+ tier in TOP", "counters Garen (8.0)"],
            ),
            PickSuggestion(
                champion_key="Camille",
                score=72.0,
                tier="S",
                reasons=["S tier in TOP"],
            ),
        ],
    )
    overlay.update_view(view)

    # Find PickCard children
    cards = overlay.findChildren(PickCard)
    assert len(cards) == 2
    assert cards[0].suggestion.champion_key == "Darius"


def test_update_view_clears_picks_on_empty(qtbot) -> None:  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    # First with suggestions
    overlay.update_view(
        SessionView(
            suggestions=[
                PickSuggestion(champion_key="X", score=50, tier="A", reasons=[]),
            ]
        )
    )
    assert len(overlay.findChildren(PickCard)) == 1
    # Then empty
    overlay.update_view(SessionView(suggestions=[]))
    qtbot.wait(10)  # let deleteLater process
    assert len(overlay.findChildren(PickCard)) == 0


def test_hotkeys_are_registered(qtbot) -> None:  # type: ignore[no-untyped-def]
    """Verify hotkey shortcuts exist with the expected key sequences.

    Real key-event handling lives in the masterplan §5.9 manual checklist —
    pytest-qt + offscreen + QShortcut is known finicky for synthetic
    keyClick events.
    """
    from PyQt6.QtGui import QKeySequence

    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    assert overlay._refresh_shortcut.key() == QKeySequence(MainOverlay.HOTKEY_REFRESH)
    assert overlay._hide_shortcut.key() == QKeySequence(MainOverlay.HOTKEY_HIDE)


def test_refresh_shortcut_emits_signal(qtbot) -> None:  # type: ignore[no-untyped-def]
    """The refresh shortcut's activated signal is wired to refresh_requested."""
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    with qtbot.waitSignal(overlay.refresh_requested, timeout=500):
        overlay._refresh_shortcut.activated.emit()


def test_status_bar_state_transitions(qtbot) -> None:  # type: ignore[no-untyped-def]
    overlay = MainOverlay()
    qtbot.addWidget(overlay)
    for state in ("waiting", "connected", "reconnecting", "disconnected"):
        overlay.update_view(SessionView(connection_state=state))
        assert overlay.status_bar.state == state


def test_frameless_flag_sets_window_hint(qtbot) -> None:  # type: ignore[no-untyped-def]
    from PyQt6.QtCore import Qt
    w = MainOverlay(frameless=True, always_on_top=True)
    qtbot.addWidget(w)
    flags = w.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
