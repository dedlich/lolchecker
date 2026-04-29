"""Tests for the state-invariant validator (charter step C4)."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

from champ_assistant.lcda.objectives import ObjectiveTimer
from champ_assistant.state_validator import (
    Issue,
    StateValidator,
    validate_objective,
    validate_snapshot,
)


# ----------------------------------------------------------------------
# Pure validators — objective timer
# ----------------------------------------------------------------------
def _obj(
    *,
    next_spawn: Optional[float] = None,
    last_killed: Optional[float] = None,
) -> ObjectiveTimer:
    return ObjectiveTimer(
        name="Dragon",
        next_spawn_seconds=next_spawn,
        last_killed_seconds=last_killed,
    )


def test_valid_objective_produces_no_issues() -> None:
    issues = validate_objective(_obj(next_spawn=420.0, last_killed=120.0), 200.0)
    assert issues == []


def test_unkilled_objective_is_valid() -> None:
    """Pre-first-kill state: next_spawn set, last_killed None — valid."""
    issues = validate_objective(_obj(next_spawn=300.0, last_killed=None), 100.0)
    assert issues == []


def test_negative_next_spawn_is_an_error() -> None:
    issues = validate_objective(_obj(next_spawn=-1.0), 100.0)
    assert any(
        i.severity == "error" and i.category == "timer"
        and "next_spawn" in i.message
        for i in issues
    )


def test_negative_last_killed_is_an_error() -> None:
    issues = validate_objective(_obj(next_spawn=400.0, last_killed=-5.0), 100.0)
    assert any(
        "last_killed" in i.message and "< 0" in i.message
        for i in issues
    )


def test_kill_in_the_future_is_an_error() -> None:
    """A kill timestamp far ahead of game_time is impossible —
    indicates LCDA event/poll desync."""
    issues = validate_objective(
        _obj(next_spawn=1000.0, last_killed=500.0), game_time=100.0,
    )
    assert any("future" in i.message for i in issues)


def test_kill_within_tolerance_is_accepted() -> None:
    """LCDA jitter: a few hundred ms ahead of game_time is the
    happy path, not a violation."""
    issues = validate_objective(
        _obj(next_spawn=400.0, last_killed=100.5), game_time=100.0,
    )
    assert issues == []


# ----------------------------------------------------------------------
# Snapshot-level validation
# ----------------------------------------------------------------------
@dataclass
class _Player:
    summoner_name: str = "X"
    level: int = 5
    items_value: int = 0


@dataclass
class _Snap:
    game_time: float = 0.0
    objectives: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    enemies: list = field(default_factory=list)


def test_valid_snapshot_produces_no_issues() -> None:
    snap = _Snap(
        game_time=600.0,
        objectives=[_obj(next_spawn=900.0, last_killed=600.0)],
        allies=[_Player(level=10, items_value=4500)],
    )
    assert validate_snapshot(snap) == []


def test_negative_game_time_short_circuits() -> None:
    """Bogus game_time would produce a flood of timer false-positives.
    The validator must surface ONE error and bail."""
    snap = _Snap(
        game_time=-5.0,
        objectives=[_obj(next_spawn=400.0, last_killed=100.0)],
    )
    issues = validate_snapshot(snap)
    assert len(issues) == 1
    assert issues[0].category == "snapshot"


def test_none_snapshot_is_valid() -> None:
    """Pre-game window — no snapshot to validate, no issues."""
    assert validate_snapshot(None) == []


def test_negative_player_level_is_a_warning() -> None:
    snap = _Snap(
        game_time=300.0,
        allies=[_Player(level=-1, items_value=2000)],
    )
    issues = validate_snapshot(snap)
    assert any(
        i.severity == "warning" and i.category == "player"
        and "level" in i.message
        for i in issues
    )


def test_negative_items_value_is_a_warning() -> None:
    snap = _Snap(
        game_time=300.0,
        enemies=[_Player(items_value=-100)],
    )
    issues = validate_snapshot(snap)
    assert any("items_value" in i.message for i in issues)


# ----------------------------------------------------------------------
# Live observer wiring
# ----------------------------------------------------------------------
class _FakeStore:
    """Minimal StateStore stand-in: subscribers receive (old, new) on
    every ``push``."""

    def __init__(self) -> None:
        self._listeners: list = []

    def subscribe(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def push(self, old, new) -> None:
        for cb in list(self._listeners):
            cb(old, new)


def test_validator_forwards_issues_to_handler() -> None:
    received: list[list[Issue]] = []
    store = _FakeStore()
    validator = StateValidator(store, on_issues=received.append)

    snap = _Snap(
        game_time=600.0,
        objectives=[_obj(next_spawn=-1.0, last_killed=300.0)],
    )
    store.push(
        SimpleNamespace(lcda_snapshot=None),
        SimpleNamespace(lcda_snapshot=snap),
    )
    assert len(received) == 1
    assert any("next_spawn" in i.message for i in received[0])
    validator.stop()


def test_validator_skips_unchanged_snapshot() -> None:
    """StateStore notifies on every update — but we should only
    re-validate when the snapshot reference actually changed."""
    received: list[list[Issue]] = []
    store = _FakeStore()
    StateValidator(store, on_issues=received.append)

    snap = _Snap(
        game_time=600.0,
        objectives=[_obj(next_spawn=-1.0, last_killed=300.0)],
    )
    state = SimpleNamespace(lcda_snapshot=snap)
    store.push(SimpleNamespace(lcda_snapshot=None), state)
    store.push(state, state)  # same reference — no re-validation
    assert len(received) == 1


def test_stop_unsubscribes() -> None:
    received: list[list[Issue]] = []
    store = _FakeStore()
    validator = StateValidator(store, on_issues=received.append)
    validator.stop()
    snap = _Snap(
        game_time=600.0,
        objectives=[_obj(next_spawn=-1.0, last_killed=300.0)],
    )
    store.push(
        SimpleNamespace(lcda_snapshot=None),
        SimpleNamespace(lcda_snapshot=snap),
    )
    assert received == []
