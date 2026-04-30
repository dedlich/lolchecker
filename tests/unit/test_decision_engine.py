"""Tests for the decision engine (charter B1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from champ_assistant.advisor.decision_engine import (
    DRAKE_PRIORITY_WINDOW_S,
    GOLD_DEFICIT_THRESHOLD,
    GOLD_LEAD_THRESHOLD,
    LEVEL_GAP_THRESHOLD,
    Recommendation,
    evaluate,
    rule_drake_give_up,
    rule_drake_priority,
    rule_far_behind_safe,
    rule_gold_lead_push,
    rule_level_deficit,
)


@dataclass
class _Aggregate:
    items_value: int = 0


@dataclass
class _Player:
    summoner_name: str = "X"
    level: int = 1


@dataclass
class _Objective:
    name: str
    next_spawn: float | None
    last_killed: float | None = None

    @property
    def next_spawn_seconds(self):
        return self.next_spawn

    @property
    def last_killed_seconds(self):
        return self.last_killed

    def remaining(self, game_time: float) -> Optional[float]:
        if self.next_spawn is None:
            return None
        return max(0.0, self.next_spawn - game_time)


@dataclass
class _Snap:
    game_time: float = 600.0
    ally_aggregate: _Aggregate = field(default_factory=_Aggregate)
    enemy_aggregate: _Aggregate = field(default_factory=_Aggregate)
    allies: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    objectives: list = field(default_factory=list)


def _drake_in(seconds: float) -> _Objective:
    """Build a Dragon objective spawning ``seconds`` from now (game_time=600)."""
    return _Objective(name="Dragon", next_spawn=600.0 + seconds, last_killed=300.0)


# ----------------------------------------------------------------------
# Drake priority
# ----------------------------------------------------------------------
def test_drake_priority_fires_when_drake_close_and_not_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=15000),
        enemy_aggregate=_Aggregate(items_value=15000),
        objectives=[_drake_in(20)],
    )
    rec = rule_drake_priority(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.category == "objective"
    assert "Drache" in rec.text


def test_drake_priority_silent_when_drake_far_away() -> None:
    snap = _Snap(objectives=[_drake_in(DRAKE_PRIORITY_WINDOW_S + 60)])
    assert rule_drake_priority(snap) is None


def test_drake_priority_silent_when_no_drake_data() -> None:
    """No Dragon objective in the snapshot — engine doesn't fabricate one."""
    snap = _Snap(objectives=[])
    assert rule_drake_priority(snap) is None


def test_drake_priority_yields_to_give_up_when_far_behind() -> None:
    """Behind by big margin → priority rule recuses itself; give_up
    rule takes over. Avoids contradictory recommendations."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=10000),
        enemy_aggregate=_Aggregate(items_value=20000),
        allies=[_Player(level=8)],
        enemies=[_Player(level=12)],
        objectives=[_drake_in(20)],
    )
    assert rule_drake_priority(snap) is None
    assert rule_drake_give_up(snap) is not None


# ----------------------------------------------------------------------
# Drake give-up
# ----------------------------------------------------------------------
def test_drake_give_up_fires_when_far_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=10000),
        enemy_aggregate=_Aggregate(items_value=16000),
        objectives=[_drake_in(15)],
    )
    rec = rule_drake_give_up(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert "abgeben" in rec.text


def test_drake_give_up_silent_when_only_slightly_behind() -> None:
    """Threshold matters — 2k behind shouldn't trigger giving up
    drake. Borderline games still play for objectives."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=14000),
        enemy_aggregate=_Aggregate(items_value=16000),
        objectives=[_drake_in(15)],
    )
    assert rule_drake_give_up(snap) is None


# ----------------------------------------------------------------------
# Gold lead / deficit / level
# ----------------------------------------------------------------------
def test_gold_lead_push_fires_when_clearly_ahead() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=20000),
        enemy_aggregate=_Aggregate(items_value=15000),
    )
    rec = rule_gold_lead_push(snap)
    assert rec is not None
    assert "+5000" in rec.text or "5000" in rec.text


def test_gold_lead_push_silent_below_threshold() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=15000),
        enemy_aggregate=_Aggregate(items_value=14000),
    )
    assert rule_gold_lead_push(snap) is None


def test_far_behind_safe_fires_below_minus_threshold() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=10000),
        enemy_aggregate=_Aggregate(items_value=16000),
    )
    rec = rule_far_behind_safe(snap)
    assert rec is not None
    assert rec.category == "safety"


def test_level_deficit_fires_with_significant_gap() -> None:
    snap = _Snap(
        allies=[_Player(level=8), _Player(level=8), _Player(level=8)],
        enemies=[_Player(level=11), _Player(level=11), _Player(level=11)],
    )
    rec = rule_level_deficit(snap)
    assert rec is not None
    assert "Level" in rec.text


def test_level_deficit_silent_with_small_gap() -> None:
    snap = _Snap(
        allies=[_Player(level=10)],
        enemies=[_Player(level=11)],
    )
    assert rule_level_deficit(snap) is None


# ----------------------------------------------------------------------
# evaluate() — orchestration
# ----------------------------------------------------------------------
def test_evaluate_returns_empty_for_none_snapshot() -> None:
    assert evaluate(None) == []


def test_evaluate_returns_empty_for_neutral_state() -> None:
    """Even game, no drake imminent — engine has nothing to say.
    Better silence than spam."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=15000),
        enemy_aggregate=_Aggregate(items_value=15000),
        allies=[_Player(level=10)],
        enemies=[_Player(level=10)],
    )
    assert evaluate(snap) == []


def test_evaluate_sorts_by_severity() -> None:
    """alerts first, then warns, then info."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=20000),
        enemy_aggregate=_Aggregate(items_value=15000),
        allies=[_Player(level=11)],
        enemies=[_Player(level=11)],
        objectives=[_drake_in(20)],
    )
    recs = evaluate(snap)
    assert len(recs) >= 2
    assert recs[0].severity == "alert"  # drake priority


def test_evaluate_isolates_buggy_rule() -> None:
    """A misbehaving rule that raises must not break the engine."""
    def boom(snap):
        raise RuntimeError("rule crash")

    def good(snap):
        return Recommendation("ok", "info", "tempo")

    snap = _Snap()
    out = evaluate(snap, rules=(boom, good))
    assert len(out) == 1
    assert out[0].text == "ok"
