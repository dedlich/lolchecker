"""Tests for the team gold-diff service + lane inference heuristic."""
from __future__ import annotations

from types import SimpleNamespace

from champ_assistant.game.gold_diff_service import (
    BLUE_TEAM,
    LANE_ORDER,
    RED_TEAM,
    compute_team_gold_diff,
)


def _snap(
    ally_value: int | float | None,
    enemy_value: int | float | None,
    *,
    active_team: str = BLUE_TEAM,
    enemy_team: str = RED_TEAM,
    allies: list | None = None,
    enemies: list | None = None,
):
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
        active_team=active_team,
        enemy_team=enemy_team,
        allies=allies or [],
        enemies=enemies or [],
    )


# ----------------------------------------------------------------------
# Team-level gold diff — new shape
# ----------------------------------------------------------------------
def test_none_snapshot_returns_empty_diff() -> None:
    result = compute_team_gold_diff(None)
    assert result == {
        "team_blue": 0, "team_red": 0,
        "blue_total": 0, "red_total": 0,
        "lane_breakdown": {}, "lane_champions": {},
    }


def test_missing_aggregates_returns_empty() -> None:
    """Game just started — items_value not yet computed."""
    assert compute_team_gold_diff(_snap(None, None))["team_blue"] == 0
    assert compute_team_gold_diff(_snap(1000, None))["team_blue"] == 0
    assert compute_team_gold_diff(_snap(None, 1000))["team_blue"] == 0


def test_positive_blue_diff_when_blue_active_ahead() -> None:
    """Active player on blue side, ally ahead → team_blue positive."""
    result = compute_team_gold_diff(_snap(15000, 12000))
    assert result["team_blue"] == 3000
    assert result["team_red"] == -3000


def test_signs_mirror_when_active_player_on_red_side() -> None:
    """If active player is on red side, our 'ally' is red. Blue is
    the enemy. ally_value=12000 enemy_value=15000 means blue (enemy)
    has 15000, red (ally) has 12000 → team_blue = +3000."""
    result = compute_team_gold_diff(
        _snap(12000, 15000, active_team=RED_TEAM, enemy_team=BLUE_TEAM)
    )
    assert result["team_blue"] == 3000
    assert result["team_red"] == -3000


def test_zero_diff_when_equal() -> None:
    result = compute_team_gold_diff(_snap(20000, 20000))
    assert result["team_blue"] == 0
    assert result["team_red"] == 0


def test_output_values_are_ints() -> None:
    """Spec: integer only, no float."""
    result = compute_team_gold_diff(_snap(15000.7, 12000.2))
    assert isinstance(result["team_blue"], int)
    assert isinstance(result["team_red"], int)
    assert result["team_blue"] == 3000


def test_garbage_aggregate_value_returns_empty() -> None:
    snap = SimpleNamespace(
        ally_aggregate=SimpleNamespace(items_value="oops"),
        enemy_aggregate=SimpleNamespace(items_value=12000),
        active_team=BLUE_TEAM,
        enemy_team=RED_TEAM,
        allies=[], enemies=[],
    )
    result = compute_team_gold_diff(snap)
    assert result["team_blue"] == 0
    assert result["team_red"] == 0


def test_lane_breakdown_empty_without_champion_tags() -> None:
    """No champion-tag dict passed → no lane attempt, empty breakdown."""
    result = compute_team_gold_diff(_snap(15000, 12000))
    assert result["lane_breakdown"] == {}


# ----------------------------------------------------------------------
# Lane-inference heuristic — best-effort, fail-closed
# ----------------------------------------------------------------------
def _player(
    name: str, champ: str, items_value: int,
    spell1: str = "Flash", spell2: str = "Ignite",
    team: str = BLUE_TEAM,
):
    return SimpleNamespace(
        summoner_name=name,
        champion_name=champ,
        team=team,
        spell_one=SimpleNamespace(name=spell1, cooldown=300.0),
        spell_two=SimpleNamespace(name=spell2, cooldown=180.0),
        items_value=items_value,
    )


def _full_team(side: str) -> list:
    """Build a 5-man team with one Smite holder (jungler) + canonical
    role distribution. Items_values picked so the test can verify."""
    return [
        _player(f"{side}-Top", "Garen",     2000, team=side),
        _player(f"{side}-Jng", "LeeSin",    1500, "Smite", "Flash", team=side),
        _player(f"{side}-Mid", "Ahri",      2500, team=side),
        _player(f"{side}-Adc", "Caitlyn",   2200, team=side),
        _player(f"{side}-Sup", "Lulu",       800, team=side),
    ]


CANONICAL_TAGS = {
    "Garen":   ["Fighter", "Tank"],
    "LeeSin":  ["Fighter", "Assassin"],
    "Ahri":    ["Mage", "Assassin"],
    "Caitlyn": ["Marksman"],
    "Lulu":    ["Support", "Mage"],
}


def test_lane_breakdown_with_full_canonical_teams() -> None:
    """Both sides have a smite + one tag-recognizable champion per
    lane → heuristic returns full lane breakdown."""
    blue = _full_team(BLUE_TEAM)
    red = [
        _player(f"{RED_TEAM}-Top", "Garen",     1900, team=RED_TEAM),
        _player(f"{RED_TEAM}-Jng", "LeeSin",    1400, "Smite", "Flash", team=RED_TEAM),
        _player(f"{RED_TEAM}-Mid", "Ahri",      2400, team=RED_TEAM),
        _player(f"{RED_TEAM}-Adc", "Caitlyn",   2100, team=RED_TEAM),
        _player(f"{RED_TEAM}-Sup", "Lulu",       700, team=RED_TEAM),
    ]
    snap = _snap(
        sum(p.items_value for p in blue),
        sum(p.items_value for p in red),
        allies=blue, enemies=red,
    )
    result = compute_team_gold_diff(snap, champion_tags=CANONICAL_TAGS)
    breakdown = result["lane_breakdown"]
    assert set(breakdown.keys()) == set(LANE_ORDER)
    # Each lane: blue minus red. Canonical setup has blue ahead by 100
    # in every lane.
    for lane, value in breakdown.items():
        assert value == 100, f"{lane} expected +100, got {value}"


def test_lane_breakdown_empty_when_no_smite() -> None:
    """ARAM / draft mistake / LCDA glitch — no Smite means we can't
    reliably identify the jungler. Fail closed."""
    no_smite_blue = [
        _player(f"{BLUE_TEAM}-{i}", "Garen", 2000,
                spell1="Flash", spell2="Ignite", team=BLUE_TEAM)
        for i in range(5)
    ]
    snap = _snap(10000, 10000, allies=no_smite_blue, enemies=_full_team(RED_TEAM))
    result = compute_team_gold_diff(snap, champion_tags=CANONICAL_TAGS)
    assert result["lane_breakdown"] == {}


def test_lane_breakdown_empty_when_unknown_champion_tags() -> None:
    """Champion-tag dict missing entries → fail closed."""
    blue = _full_team(BLUE_TEAM)
    red = _full_team(RED_TEAM)
    snap = _snap(10000, 10000, allies=blue, enemies=red)
    # Empty tags map → can't classify
    result = compute_team_gold_diff(snap, champion_tags={})
    # Empty dict is falsy → caller path skipped, breakdown empty.
    assert result["lane_breakdown"] == {}


def test_lane_breakdown_team_only_still_works_when_inference_fails() -> None:
    """Even when lane inference fails, team_blue/team_red must still
    be correct. Spec: 'team-level gold diff is ALWAYS authoritative'."""
    snap = _snap(15000, 12000)  # no players list = inference impossible
    result = compute_team_gold_diff(snap, champion_tags=CANONICAL_TAGS)
    assert result["team_blue"] == 3000
    assert result["lane_breakdown"] == {}


def test_deterministic_same_input_same_output() -> None:
    snap = _snap(13000, 11000)
    results = {compute_team_gold_diff(snap)["team_blue"] for _ in range(10)}
    assert results == {2000}
