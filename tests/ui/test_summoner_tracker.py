"""Smoke tests for the SummonerTrackerPanel widget."""
from __future__ import annotations

import pytest

from champ_assistant.lcda.objectives import ObjectiveTimer
from champ_assistant.lcda.players import (
    LivePlayer,
    LiveSummonerSpell,
)
from champ_assistant.lcda.source import LcdaSnapshot
from champ_assistant.ui.summoner_tracker import SummonerTrackerPanel


def _enemy(
    name: str,
    champion: str,
    spell_one: str = "Flash",
    cooldown_one: float = 300.0,
    spell_two: str = "Ignite",
    cooldown_two: float = 180.0,
) -> LivePlayer:
    return LivePlayer(
        summoner_name=name,
        champion_name=champion,
        team="CHAOS",
        spell_one=LiveSummonerSpell(name=spell_one, cooldown=cooldown_one),
        spell_two=LiveSummonerSpell(name=spell_two, cooldown=cooldown_two),
    )


def _snapshot(enemies: list[LivePlayer], game_time: float = 600.0) -> LcdaSnapshot:
    return LcdaSnapshot(
        game_time=game_time,
        game_mode="CLASSIC",
        objectives=[],
        enemies=enemies,
        active_summoner="Me",
        raw_events=[],
    )


@pytest.fixture
def panel(qtbot) -> SummonerTrackerPanel:  # type: ignore[no-untyped-def]
    p = SummonerTrackerPanel()
    qtbot.addWidget(p)
    return p


def test_panel_starts_hidden(panel: SummonerTrackerPanel) -> None:
    assert panel.isHidden() is True


def test_panel_shows_when_snapshot_arrives(panel: SummonerTrackerPanel) -> None:
    panel.update_snapshot(_snapshot([_enemy("Faker", "Ahri")]))
    assert panel.isHidden() is False


def test_panel_hides_on_none(panel: SummonerTrackerPanel) -> None:
    panel.update_snapshot(_snapshot([_enemy("Faker", "Ahri")]))
    panel.update_snapshot(None)
    assert panel.isHidden() is True


def test_click_starts_cooldown_via_tracker(panel: SummonerTrackerPanel) -> None:
    enemy = _enemy("Faker", "Ahri", spell_one="Flash", cooldown_one=300.0)
    panel.update_snapshot(_snapshot([enemy], game_time=600.0))
    # Simulate a click on the first row's flash badge by emitting the signal.
    panel._rows[0].spell_clicked.emit("Faker", "Flash")
    cd = panel.tracker().get("Faker", "Flash")
    assert cd is not None
    assert cd.cast_at == 600.0
    assert cd.cooldown == 300.0


def test_right_click_resets_cooldown(panel: SummonerTrackerPanel) -> None:
    enemy = _enemy("Faker", "Ahri", spell_one="Flash", cooldown_one=300.0)
    panel.update_snapshot(_snapshot([enemy], game_time=600.0))
    panel._rows[0].spell_clicked.emit("Faker", "Flash")
    panel._rows[0].spell_right_clicked.emit("Faker", "Flash")
    assert panel.tracker().get("Faker", "Flash") is None


def test_unknown_spell_click_is_ignored(panel: SummonerTrackerPanel) -> None:
    enemy = _enemy("X", "Y", spell_one="Mystery", cooldown_one=0.0)
    panel.update_snapshot(_snapshot([enemy], game_time=600.0))
    panel._rows[0].spell_clicked.emit("X", "Mystery")
    assert panel.tracker().get("X", "Mystery") is None


def test_gc_drops_ready_cooldowns_on_next_tick(panel: SummonerTrackerPanel) -> None:
    enemy = _enemy("Faker", "Ahri", spell_one="Flash", cooldown_one=300.0)
    panel.update_snapshot(_snapshot([enemy], game_time=600.0))
    panel._rows[0].spell_clicked.emit("Faker", "Flash")
    assert panel.tracker().get("Faker", "Flash") is not None
    # Advance past the cooldown window — gc removes the ready entry.
    panel.update_snapshot(_snapshot([enemy], game_time=1000.0))
    assert panel.tracker().get("Faker", "Flash") is None


def test_extra_rows_hidden_when_fewer_than_five_enemies(
    panel: SummonerTrackerPanel,
) -> None:
    panel.update_snapshot(_snapshot([_enemy("A", "Ahri"), _enemy("B", "Yasuo")]))
    visible = [r for r in panel._rows if not r.isHidden()]
    assert len(visible) == 2


def test_objectives_field_unused_does_not_crash(panel: SummonerTrackerPanel) -> None:
    snap = LcdaSnapshot(
        game_time=600.0,
        game_mode="CLASSIC",
        objectives=[ObjectiveTimer(name="Dragon", next_spawn_seconds=900.0,
                                   last_killed_seconds=None)],
        enemies=[_enemy("X", "Y")],
        active_summoner="Me",
        raw_events=[],
    )
    panel.update_snapshot(snap)  # should not raise
    assert panel.isHidden() is False
