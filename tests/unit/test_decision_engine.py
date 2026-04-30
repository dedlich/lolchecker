"""Tests for the decision engine (charter B1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from champ_assistant.advisor.decision_engine import (
    BARON_PRIORITY_WINDOW_S,
    DRAKE_PRIORITY_WINDOW_S,
    GOLD_DEFICIT_THRESHOLD,
    GOLD_LEAD_THRESHOLD,
    HERALD_LATE_GAME_S,
    KILL_DEFICIT_THRESHOLD,
    KILL_LEAD_THRESHOLD,
    LATE_GAME_S,
    LEVEL_GAP_THRESHOLD,
    Recommendation,
    evaluate,
    fight_score,
    win_probability,
    rule_baron_give_up,
    rule_baron_priority,
    rule_drake_give_up,
    rule_drake_priority,
    rule_far_behind_safe,
    rule_gold_lead_push,
    rule_herald_priority,
    rule_kill_deficit_defensive,
    rule_kill_lead_snowball,
    rule_late_game_group,
    rule_level_deficit,
)


@dataclass
class _Aggregate:
    items_value: int = 0
    kills: int = 0


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


# ----------------------------------------------------------------------
# Baron rules (mirror of drake — separate window, separate threshold)
# ----------------------------------------------------------------------
def _baron_in(seconds: float) -> _Objective:
    return _Objective(name="Baron", next_spawn=600.0 + seconds, last_killed=300.0)


def test_baron_priority_fires_when_baron_close_and_not_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=20000),
        enemy_aggregate=_Aggregate(items_value=20000),
        objectives=[_baron_in(35)],
    )
    rec = rule_baron_priority(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert "Baron" in rec.text


def test_baron_priority_uses_wider_window_than_drake() -> None:
    """40s out — within the 45s Baron window, outside the 30s
    drake window. Verifies the priorities are independently tuned."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=20000),
        enemy_aggregate=_Aggregate(items_value=20000),
        objectives=[_baron_in(40)],
    )
    assert rule_baron_priority(snap) is not None


def test_baron_give_up_fires_when_far_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=10000),
        enemy_aggregate=_Aggregate(items_value=18000),
        objectives=[_baron_in(20)],
    )
    rec = rule_baron_give_up(snap)
    assert rec is not None
    assert "abgeben" in rec.text


# ----------------------------------------------------------------------
# Herald — early-game-only rule
# ----------------------------------------------------------------------
def _herald_in(seconds: float, game_time: float) -> _Objective:
    return _Objective(name="Herald", next_spawn=game_time + seconds, last_killed=game_time)


def test_herald_priority_fires_in_early_game() -> None:
    snap = _Snap(
        game_time=10 * 60.0,
        ally_aggregate=_Aggregate(items_value=8000),
        enemy_aggregate=_Aggregate(items_value=8000),
        objectives=[_herald_in(20, 10 * 60.0)],
    )
    rec = rule_herald_priority(snap)
    assert rec is not None
    assert "Herald" in rec.text


def test_herald_priority_silent_post_despawn() -> None:
    """Herald despawns ~14:00 — rule must NOT fire after that
    even if the snapshot still carries an old Herald entry."""
    snap = _Snap(
        game_time=HERALD_LATE_GAME_S + 60.0,
        objectives=[_herald_in(20, HERALD_LATE_GAME_S + 60.0)],
    )
    assert rule_herald_priority(snap) is None


# ----------------------------------------------------------------------
# Kill-diff rules (snowball / deficit)
# ----------------------------------------------------------------------
def test_kill_lead_snowball_fires_with_big_lead() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(kills=KILL_LEAD_THRESHOLD + 2),
        enemy_aggregate=_Aggregate(kills=0),
    )
    rec = rule_kill_lead_snowball(snap)
    assert rec is not None
    assert rec.category == "tempo"


def test_kill_lead_snowball_silent_below_threshold() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(kills=KILL_LEAD_THRESHOLD - 1),
        enemy_aggregate=_Aggregate(kills=0),
    )
    assert rule_kill_lead_snowball(snap) is None


def test_kill_deficit_defensive_fires_when_far_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(kills=0),
        enemy_aggregate=_Aggregate(kills=KILL_DEFICIT_THRESHOLD + 2),
    )
    rec = rule_kill_deficit_defensive(snap)
    assert rec is not None
    assert rec.severity == "warn"


def test_kill_diff_falls_back_to_per_player_when_aggregate_missing() -> None:
    """Some snapshots may lack the team aggregate — sum per-player
    kills as a fallback so the rule still fires."""
    snap = _Snap(
        ally_aggregate=None,
        enemy_aggregate=None,
        allies=[_Player(level=10) for _ in range(5)],
        enemies=[_Player(level=10) for _ in range(5)],
    )
    # Inject kills into the per-player records.
    for i, p in enumerate(snap.allies):
        object.__setattr__(p, "kills", 2)  # 5 × 2 = 10 ally kills
    snap.allies[0].kills = 5
    rec = rule_kill_lead_snowball(snap)
    # 5+2+2+2+2 = 13 ally kills, 0 enemy kills → strong lead
    assert rec is not None


# ----------------------------------------------------------------------
# Late-game group rule
# ----------------------------------------------------------------------
def test_late_game_group_fires_past_30_min() -> None:
    snap = _Snap(game_time=LATE_GAME_S + 60.0)
    rec = rule_late_game_group(snap)
    assert rec is not None
    assert "group" in rec.text.lower() or "5" in rec.text


def test_late_game_group_silent_pre_30_min() -> None:
    snap = _Snap(game_time=LATE_GAME_S - 60.0)
    assert rule_late_game_group(snap) is None


# ----------------------------------------------------------------------
# Composition: multiple rules can fire together
# ----------------------------------------------------------------------
def test_evaluate_composes_kill_lead_with_drake_priority() -> None:
    """Kill snowball + drake imminent → both rules fire, sorted by
    severity (drake alert first, kill-lead info second)."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=18000, kills=KILL_LEAD_THRESHOLD + 1),
        enemy_aggregate=_Aggregate(items_value=14000, kills=0),
        allies=[_Player(level=11)],
        enemies=[_Player(level=11)],
        objectives=[_drake_in(20)],
    )
    recs = evaluate(snap)
    severities = [r.severity for r in recs]
    assert "alert" in severities
    assert severities[0] == "alert"  # alert sorted first


# ----------------------------------------------------------------------
# v2 spec — Recommendation extension fields
# ----------------------------------------------------------------------
def test_recommendation_has_v2_defaults() -> None:
    """v2 added confidence / risk / ttl_s. Legacy callers that omit
    them should still get a usable Recommendation with conservative
    defaults (rule fired → moderate confidence, medium risk, 15s ttl)."""
    rec = Recommendation(text="x", severity="info", category="tempo")
    assert 0.0 <= rec.confidence <= 1.0
    assert rec.risk in ("LOW", "MEDIUM", "HIGH")
    assert rec.ttl_s > 0


def test_recommendation_accepts_explicit_v2_fields() -> None:
    rec = Recommendation(
        text="Force fight", severity="alert", category="objective",
        confidence=0.9, risk="LOW", ttl_s=30.0,
    )
    assert rec.confidence == 0.9
    assert rec.risk == "LOW"
    assert rec.ttl_s == 30.0


# ----------------------------------------------------------------------
# Layer 2 — fight_score (weighted sum of advantage signals)
# ----------------------------------------------------------------------
def test_fight_score_zero_for_neutral_state() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=15000, kills=10),
        enemy_aggregate=_Aggregate(items_value=15000, kills=10),
        allies=[_Player(level=10)],
        enemies=[_Player(level=10)],
    )
    assert abs(fight_score(snap)) < 0.05


def test_fight_score_positive_when_clearly_ahead() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=20000, kills=15),
        enemy_aggregate=_Aggregate(items_value=15000, kills=10),
        allies=[_Player(level=12)],
        enemies=[_Player(level=11)],
    )
    score = fight_score(snap)
    assert score > 0.3


def test_fight_score_negative_when_far_behind() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=10000, kills=5),
        enemy_aggregate=_Aggregate(items_value=18000, kills=15),
        allies=[_Player(level=10)],
        enemies=[_Player(level=12)],
    )
    score = fight_score(snap)
    assert score < -0.3


def test_fight_score_clamped_to_unit_range() -> None:
    """Saturate the inputs — score must stay in [-1, 1]."""
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=999_999, kills=999),
        enemy_aggregate=_Aggregate(items_value=0, kills=0),
        allies=[_Player(level=18)],
        enemies=[_Player(level=1)],
    )
    assert -1.0 <= fight_score(snap) <= 1.0


def test_fight_score_none_snapshot_returns_zero() -> None:
    assert fight_score(None) == 0.0


# ----------------------------------------------------------------------
# Layer 3 — win_probability (logistic of fight_score)
# ----------------------------------------------------------------------
def test_win_probability_neutral_state_is_about_half() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=15000, kills=10),
        enemy_aggregate=_Aggregate(items_value=15000, kills=10),
        allies=[_Player(level=10)],
        enemies=[_Player(level=10)],
    )
    assert abs(win_probability(snap) - 0.5) < 0.05


def test_win_probability_strong_lead_above_threshold() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=22000, kills=20),
        enemy_aggregate=_Aggregate(items_value=12000, kills=5),
        allies=[_Player(level=14)],
        enemies=[_Player(level=10)],
    )
    assert win_probability(snap) > 0.8


def test_win_probability_strong_deficit_below_threshold() -> None:
    snap = _Snap(
        ally_aggregate=_Aggregate(items_value=8000, kills=2),
        enemy_aggregate=_Aggregate(items_value=20000, kills=18),
        allies=[_Player(level=8)],
        enemies=[_Player(level=14)],
    )
    assert win_probability(snap) < 0.2


def test_win_probability_bounded_zero_to_one() -> None:
    """The logistic clamp must stay in [0, 1] regardless of input."""
    for items in (0, 999_999):
        snap = _Snap(
            ally_aggregate=_Aggregate(items_value=items, kills=0),
            enemy_aggregate=_Aggregate(items_value=999_999 - items, kills=0),
            allies=[_Player(level=10)],
            enemies=[_Player(level=10)],
        )
        p = win_probability(snap)
        assert 0.0 <= p <= 1.0
