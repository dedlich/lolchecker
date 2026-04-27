"""Smoke tests for the in-game objective timer panel."""
from __future__ import annotations

import pytest

from champ_assistant.lcda.objectives import ObjectiveTimer
from champ_assistant.lcda.source import LcdaSnapshot
from champ_assistant.ui.objective_panel import ObjectivePanel


def _snap(*, game_time: float, objectives: list[ObjectiveTimer]) -> LcdaSnapshot:
    return LcdaSnapshot(
        game_time=game_time,
        game_mode="CLASSIC",
        objectives=objectives,
        enemies=[],
        active_summoner="",
        raw_events=[],
    )


@pytest.fixture
def panel(qtbot) -> ObjectivePanel:  # type: ignore[no-untyped-def]
    p = ObjectivePanel()
    qtbot.addWidget(p)
    return p


def test_panel_starts_hidden(panel: ObjectivePanel) -> None:
    assert panel.isHidden() is True


def test_panel_shows_when_snapshot_arrives(panel: ObjectivePanel) -> None:
    snap = _snap(
        game_time=600.0,
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=900.0,
                           last_killed_seconds=600.0, last_killer="Kindred",
                           detail="Cloud"),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
    )
    panel.update_snapshot(snap)
    assert panel.isHidden() is False
    assert panel._game_time_label.text() == "10:00"
    drag_row = panel._rows["Dragon"]
    assert drag_row._timer_label.text() == "5:00"
    detail = drag_row._detail_label.text()
    assert "Cloud" in detail
    assert "Kindred" in detail


def test_panel_hides_on_none_snapshot(panel: ObjectivePanel) -> None:
    snap = _snap(
        game_time=120.0,
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=300.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
    )
    panel.update_snapshot(snap)
    assert panel.isHidden() is False
    panel.update_snapshot(None)
    assert panel.isHidden() is True


def test_panel_shows_up_when_remaining_is_zero(panel: ObjectivePanel) -> None:
    snap = _snap(
        game_time=900.0,
        objectives=[
            ObjectiveTimer(name="Dragon", next_spawn_seconds=900.0,
                           last_killed_seconds=600.0),
            ObjectiveTimer(name="Baron", next_spawn_seconds=1500.0,
                           last_killed_seconds=None),
            ObjectiveTimer(name="Herald", next_spawn_seconds=840.0,
                           last_killed_seconds=None),
        ],
    )
    panel.update_snapshot(snap)
    assert panel._rows["Dragon"]._timer_label.text() == "UP"
    # Herald already past its first spawn (840) at 900 → also UP
    assert panel._rows["Herald"]._timer_label.text() == "UP"
