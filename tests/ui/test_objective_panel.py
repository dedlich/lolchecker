"""Smoke tests for the in-game objective timer panel."""
from __future__ import annotations

import pytest

from champ_assistant.lcda.objectives import ObjectiveTimer
from champ_assistant.lcda.source import LcdaSnapshot
from champ_assistant.ui.objective_panel import ObjectivePanel


@pytest.fixture
def panel(qtbot) -> ObjectivePanel:  # type: ignore[no-untyped-def]
    p = ObjectivePanel()
    qtbot.addWidget(p)
    return p


def test_panel_starts_hidden(panel: ObjectivePanel) -> None:
    assert panel.isHidden() is True


def test_panel_shows_when_snapshot_arrives(panel: ObjectivePanel) -> None:
    snap = LcdaSnapshot(
        game_time=600.0,
        game_mode="CLASSIC",
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=900.0,
                           last_killed_seconds=600.0, last_killer="Kindred",
                           detail="Cloud"),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
        raw_events=[],
    )
    panel.update_snapshot(snap)
    assert panel.isHidden() is False
    # Game time formatted as M:SS in the header.
    assert "10:00" in panel._game_time_label.text()
    # Dragon row shows the remaining time (900 - 600 = 300s = 5:00) and detail.
    drag_row = panel._rows["Dragon"]
    assert drag_row["timer"].text() == "5:00"
    assert "Cloud" in drag_row["detail"].text()
    assert "Kindred" in drag_row["detail"].text()


def test_panel_hides_on_none_snapshot(panel: ObjectivePanel) -> None:
    snap = LcdaSnapshot(
        game_time=120.0,
        game_mode="CLASSIC",
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=300.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
        raw_events=[],
    )
    panel.update_snapshot(snap)
    assert panel.isHidden() is False
    panel.update_snapshot(None)
    assert panel.isHidden() is True


def test_panel_shows_up_when_remaining_is_zero(panel: ObjectivePanel) -> None:
    snap = LcdaSnapshot(
        game_time=900.0,
        game_mode="CLASSIC",
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=900.0,
                           last_killed_seconds=600.0),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
        raw_events=[],
    )
    panel.update_snapshot(snap)
    assert panel._rows["Dragon"]["timer"].text() == "UP"
    # Herald already past its first spawn (840) at 900 → also UP
    assert panel._rows["Herald"]["timer"].text() == "UP"
