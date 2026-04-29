"""Tests for the team-level gold-diff service."""
from __future__ import annotations

from types import SimpleNamespace

from champ_assistant.game.gold_diff_service import compute_team_gold_diff


def _snap(ally_value: int | float | None, enemy_value: int | float | None):
    """Build a minimal mock LcdaSnapshot with team aggregates."""
    return SimpleNamespace(
        ally_aggregate=(
            None if ally_value is None
            else SimpleNamespace(items_value=ally_value)
        ),
        enemy_aggregate=(
            None if enemy_value is None
            else SimpleNamespace(items_value=enemy_value)
        ),
    )


def test_none_snapshot_returns_zero() -> None:
    assert compute_team_gold_diff(None) == {"team": 0}


def test_missing_aggregates_returns_zero() -> None:
    """Game just started — items_value not yet computed."""
    assert compute_team_gold_diff(_snap(None, None)) == {"team": 0}
    assert compute_team_gold_diff(_snap(1000, None)) == {"team": 0}
    assert compute_team_gold_diff(_snap(None, 1000)) == {"team": 0}


def test_positive_diff_when_ally_ahead() -> None:
    assert compute_team_gold_diff(_snap(15000, 12000)) == {"team": 3000}


def test_negative_diff_when_enemy_ahead() -> None:
    assert compute_team_gold_diff(_snap(10000, 13500)) == {"team": -3500}


def test_zero_diff_when_equal() -> None:
    assert compute_team_gold_diff(_snap(20000, 20000)) == {"team": 0}


def test_output_is_int() -> None:
    """Spec: integer only, no float, no rounding errors."""
    result = compute_team_gold_diff(_snap(15000.7, 12000.2))
    assert isinstance(result["team"], int)
    # Truncated to int via int(15000.7) - int(12000.2) = 15000 - 12000 = 3000
    assert result["team"] == 3000


def test_garbage_aggregate_value_returns_zero() -> None:
    """A bogus type in items_value (e.g. None) — must fall back to 0
    rather than crash."""
    snap = SimpleNamespace(
        ally_aggregate=SimpleNamespace(items_value="oops"),
        enemy_aggregate=SimpleNamespace(items_value=12000),
    )
    assert compute_team_gold_diff(snap) == {"team": 0}


def test_deterministic_same_input_same_output() -> None:
    snap = _snap(13000, 11000)
    results = {compute_team_gold_diff(snap)["team"] for _ in range(10)}
    assert results == {2000}
