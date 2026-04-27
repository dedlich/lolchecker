"""Tests for the Dragon/Baron/Herald spawn-time tracker."""
from __future__ import annotations

import pytest

from champ_assistant.lcda.objectives import (
    BARON_FIRST_SPAWN,
    BARON_RESPAWN,
    DRAGON_FIRST_SPAWN,
    DRAGON_RESPAWN,
    HERALD_DESPAWN,
    HERALD_FIRST_SPAWN,
    HERALD_RESPAWN,
    ObjectiveTimer,
    compute_objectives,
)


def test_no_kills_yet_returns_first_spawn_constants() -> None:
    objs = compute_objectives([], game_time=120.0)
    by_name = {o.name: o for o in objs}
    assert by_name["Dragon"].next_spawn_seconds == DRAGON_FIRST_SPAWN
    assert by_name["Baron"].next_spawn_seconds == BARON_FIRST_SPAWN
    assert by_name["Herald"].next_spawn_seconds == HERALD_FIRST_SPAWN
    assert all(o.last_killed_seconds is None for o in objs)


def test_dragon_kill_uses_kill_time_plus_respawn() -> None:
    events = [
        {"EventID": 1, "EventName": "DragonKill", "EventTime": 600.0,
         "KillerName": "Kindred", "DragonType": "Cloud"},
    ]
    objs = compute_objectives(events, game_time=650.0)
    drag = next(o for o in objs if o.name == "Dragon")
    assert drag.next_spawn_seconds == 600.0 + DRAGON_RESPAWN
    assert drag.last_killed_seconds == 600.0
    assert drag.last_killer == "Kindred"
    assert drag.detail == "Cloud"


def test_multiple_dragon_kills_uses_latest() -> None:
    events = [
        {"EventName": "DragonKill", "EventTime": 600.0, "DragonType": "Earth"},
        {"EventName": "DragonKill", "EventTime": 1100.0, "DragonType": "Cloud"},
    ]
    objs = compute_objectives(events, game_time=1200.0)
    drag = next(o for o in objs if o.name == "Dragon")
    assert drag.last_killed_seconds == 1100.0
    assert drag.detail == "Cloud"
    assert drag.next_spawn_seconds == 1100.0 + DRAGON_RESPAWN


def test_baron_kill_uses_baron_respawn() -> None:
    events = [{"EventName": "BaronKill", "EventTime": 1500.0, "KillerName": "X"}]
    objs = compute_objectives(events, game_time=1600.0)
    baron = next(o for o in objs if o.name == "Baron")
    assert baron.next_spawn_seconds == 1500.0 + BARON_RESPAWN
    assert baron.last_killer == "X"


def test_herald_respawn_within_window() -> None:
    events = [{"EventName": "HeraldKill", "EventTime": 800.0}]
    objs = compute_objectives(events, game_time=900.0)
    herald = next(o for o in objs if o.name == "Herald")
    assert herald.next_spawn_seconds == 800.0 + HERALD_RESPAWN


def test_herald_does_not_respawn_after_despawn_window() -> None:
    # Killed at ~17:00 (1020s) → next would be ~23:00 (1380s) > despawn (1195s)
    events = [{"EventName": "HeraldKill", "EventTime": 1020.0}]
    objs = compute_objectives(events, game_time=1100.0)
    herald = next(o for o in objs if o.name == "Herald")
    assert herald.next_spawn_seconds is None
    assert herald.last_killed_seconds == 1020.0


def test_herald_after_despawn_with_no_kill_returns_none() -> None:
    objs = compute_objectives([], game_time=HERALD_DESPAWN + 60)
    herald = next(o for o in objs if o.name == "Herald")
    assert herald.next_spawn_seconds is None


def test_remaining_floors_to_zero() -> None:
    timer = ObjectiveTimer(
        name="Dragon",
        next_spawn_seconds=600.0,
        last_killed_seconds=300.0,
    )
    assert timer.remaining(700.0) == 0.0
    assert timer.remaining(550.0) == pytest.approx(50.0)


def test_remaining_returns_none_when_wont_respawn() -> None:
    timer = ObjectiveTimer(
        name="Herald",
        next_spawn_seconds=None,
        last_killed_seconds=1100.0,
    )
    assert timer.remaining(1200.0) is None
    assert timer.is_up(1200.0) is False


def test_is_up_when_remaining_is_zero() -> None:
    timer = ObjectiveTimer(
        name="Dragon",
        next_spawn_seconds=600.0,
        last_killed_seconds=300.0,
    )
    assert timer.is_up(600.5) is True
    assert timer.is_up(599.0) is False
