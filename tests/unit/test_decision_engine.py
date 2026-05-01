"""Tests for the decision engine (charter B1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

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
    _drake_stack_count,
    _suppress_dominated,
    evaluate,
    fight_score,
    win_probability,
    rule_baron_give_up,
    rule_baron_priority,
    rule_baron_window,
    rule_drake_give_up,
    rule_drake_priority,
    rule_dragon_window,
    rule_far_behind_safe,
    rule_fight_opportunity,
    rule_gold_lead_push,
    rule_herald_priority,
    rule_kill_deficit_defensive,
    rule_kill_lead_snowball,
    rule_late_game_group,
    rule_level_deficit,
    rule_numbers_advantage,
    rule_numbers_disadvantage,
)


@dataclass
class _Aggregate:
    items_value: int = 0
    kills: int = 0


@dataclass
class _Player:
    summoner_name: str = "X"
    champion_name: str = ""
    level: int = 1
    is_alive: bool = True
    respawn_timer: float | None = None


@dataclass
class _Objective:
    name: str
    next_spawn: float | None
    last_killed: float | None = None
    detail: str | None = None

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
    raw_events: list = field(default_factory=list)
    active_team: str = ""
    game_result: str = ""
    new_spikes: list = field(default_factory=list)


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
    Better silence than spam. game_time=900 is past the void-grub window (14:00)."""
    snap = _Snap(
        game_time=900.0,
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


# ----------------------------------------------------------------------
# Regression: empty-team guard
# (Bug: rules fired with allies=[] before team identity established,
#  producing spurious 0v5 safety spam in the first LCDA ticks.)
# ----------------------------------------------------------------------

def test_numbers_disadvantage_silent_when_allies_empty() -> None:
    snap = _Snap(allies=[], enemies=[_Player() for _ in range(5)])
    assert rule_numbers_disadvantage(snap) is None


def test_numbers_advantage_silent_when_allies_empty() -> None:
    snap = _Snap(allies=[], enemies=[_Player() for _ in range(5)])
    assert rule_numbers_advantage(snap) is None


def test_dragon_window_silent_when_allies_empty() -> None:
    snap = _Snap(allies=[], enemies=[_Player() for _ in range(5)], objectives=[_drake_in(15)])
    assert rule_dragon_window(snap) is None


def test_baron_window_silent_when_allies_empty() -> None:
    snap = _Snap(
        allies=[],
        enemies=[_Player() for _ in range(5)],
        objectives=[_Objective(name="Baron", next_spawn=600.0 + 30)],
    )
    assert rule_baron_window(snap) is None


# ----------------------------------------------------------------------
# Regression: KillerName champion-name format in drake stack counting
# (Bug: _drake_stack_count matched KillerName against summoner_name only;
#  LCDA historically emits champion display names, so stacks always read 0.)
# ----------------------------------------------------------------------

def _dragon_kill_event(killer_name: str) -> dict:
    return {"EventName": "DragonKill", "KillerName": killer_name, "EventTime": 300.0}


def test_drake_stack_count_matches_champion_name() -> None:
    """KillerName = "Kindred" (champion) — player has summoner_name "Player2"."""
    ally = _Player(summoner_name="Player2", champion_name="Kindred")
    snap = _Snap(
        allies=[ally],
        raw_events=[_dragon_kill_event("Kindred")],
    )
    assert _drake_stack_count(snap) == 1


def test_drake_stack_count_matches_summoner_name() -> None:
    """KillerName = "Player2" (summoner) — also works (post-Riot-ID format)."""
    ally = _Player(summoner_name="Player2", champion_name="Kindred")
    snap = _Snap(
        allies=[ally],
        raw_events=[_dragon_kill_event("Player2")],
    )
    assert _drake_stack_count(snap) == 1


def test_drake_stack_count_zero_when_enemy_killed() -> None:
    ally = _Player(summoner_name="Player1", champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        raw_events=[_dragon_kill_event("Kindred")],  # enemy champion, not in allies
    )
    assert _drake_stack_count(snap) == 0


def test_drake_stack_count_accumulates_multiple_events() -> None:
    ally = _Player(summoner_name="P1", champion_name="Kindred")
    snap = _Snap(
        allies=[ally],
        raw_events=[
            _dragon_kill_event("Kindred"),
            _dragon_kill_event("Kindred"),
            _dragon_kill_event("Kindred"),
        ],
    )
    assert _drake_stack_count(snap) == 3


# ----------------------------------------------------------------------
# Regression: _suppress_dominated Rule 4 — fight_bad contradicts obj take
# (Bug: "Fights meiden" could appear alongside "Baron JETZT".)
# ----------------------------------------------------------------------

def _rec(kind: str, severity: str = "info") -> Recommendation:
    return Recommendation(text=kind, severity=severity, category="tempo", kind=kind)


def test_suppress_fight_bad_removed_when_dragon_free() -> None:
    recs = [_rec("dragon_free", "alert"), _rec("fight_bad", "warn")]
    result = _suppress_dominated(recs)
    assert all(r.kind != "fight_bad" for r in result)
    assert any(r.kind == "dragon_free" for r in result)


def test_suppress_fight_bad_removed_when_baron_take() -> None:
    recs = [_rec("baron_take", "alert"), _rec("fight_bad", "warn")]
    result = _suppress_dominated(recs)
    assert all(r.kind != "fight_bad" for r in result)


def test_suppress_fight_bad_kept_when_no_obj_take() -> None:
    """fight_bad survives if there's no active objective-take call."""
    recs = [_rec("gold_lead", "info"), _rec("fight_bad", "warn")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "fight_bad" for r in result)


def test_suppress_numbers_disadv_removes_all_offensive() -> None:
    """Rule 1 — numbers_disadv present → drop all offensive kinds."""
    offensive_kinds = ["fight", "numbers_adv", "gold_lead", "kill_lead",
                       "dragon_take", "dragon_free", "baron_take", "baron_free"]
    recs = [_rec("numbers_disadv", "alert")] + [_rec(k) for k in offensive_kinds]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert result_kinds == {"numbers_disadv"}


def test_suppress_dragon_free_absorbs_numbers_adv() -> None:
    """Rule 2 — dragon_free present → standalone numbers_adv removed."""
    recs = [_rec("dragon_free", "alert"), _rec("numbers_adv", "alert")]
    result = _suppress_dominated(recs)
    assert all(r.kind != "numbers_adv" for r in result)
    assert any(r.kind == "dragon_free" for r in result)


def test_suppress_fight_absorbs_gold_and_kill_lead() -> None:
    """Rule 3 — fight rec present → gold_lead and kill_lead suppressed."""
    recs = [_rec("fight", "alert"), _rec("gold_lead", "info"), _rec("kill_lead", "info")]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert "fight" in result_kinds
    assert "gold_lead" not in result_kinds
    assert "kill_lead" not in result_kinds


# ----------------------------------------------------------------------
# Numbers rules: dead players detected via is_alive + respawn_timer
# ----------------------------------------------------------------------

def test_numbers_disadvantage_detects_dead_ally() -> None:
    """Ally with respawn_timer=15, is_alive=False counts as dead."""
    dead_ally = _Player(is_alive=False, respawn_timer=15.0)
    alive_allies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(4)]
    five_enemies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(5)]
    snap = _Snap(allies=[dead_ally] + alive_allies, enemies=five_enemies)
    rec = rule_numbers_disadvantage(snap)
    assert rec is not None
    assert "4v5" in rec.text


def test_numbers_advantage_detects_dead_enemy() -> None:
    """Enemy with is_alive=False counted as dead — we have numbers advantage."""
    dead_enemy = _Player(is_alive=False, respawn_timer=20.0)
    alive_enemies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(4)]
    five_allies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(5)]
    snap = _Snap(allies=five_allies, enemies=[dead_enemy] + alive_enemies)
    rec = rule_numbers_advantage(snap)
    assert rec is not None
    assert "5v4" in rec.text


# ----------------------------------------------------------------------
# Turret tracking — _parse_turret_name + _enemy_turrets_down
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (
    _parse_turret_name,
    _enemy_turrets_down,
    _kill_streak,
    _enemy_herald_pickup,
    _active_enemy_inhibitors_down,
    _active_ally_inhibitors_down,
    rule_lane_pressure,
    rule_ace_detected,
    rule_enemy_base_exposed,
    rule_enemy_herald_danger,
    rule_enemy_inhibitor_down,
    rule_ally_inhib_down,
    rule_game_ended,
    HERALD_USAGE_WINDOW_S,
)


def _turret_killed_event(turret_name: str) -> dict:
    return {"EventName": "TurretKilled", "TurretKilled": turret_name, "EventTime": 500.0}


def test_parse_turret_name_chaos_bot_outer() -> None:
    result = _parse_turret_name("Turret_TChaos_L0_P1_MinionSpawnPos")
    assert result == ("TChaos", "L0", "P1")


def test_parse_turret_name_order_top_inner() -> None:
    result = _parse_turret_name("Turret_TOrder_L2_P2_Base")
    assert result == ("TOrder", "L2", "P2")


def test_parse_turret_name_inhibitor_tier() -> None:
    result = _parse_turret_name("Turret_TChaos_L1_P3_Base")
    assert result == ("TChaos", "L1", "P3")


def test_parse_turret_name_invalid_returns_none() -> None:
    assert _parse_turret_name("SomeRandomStructure") is None
    assert _parse_turret_name("") is None


def test_enemy_turrets_down_counts_enemy_outers_order_team() -> None:
    """active_team=ORDER → enemy side is TChaos."""
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L0_P1_MinionSpawnPos"),  # enemy Bot outer
            _turret_killed_event("Turret_TChaos_L0_P2_MinionSpawnPos"),  # enemy Bot inner
        ],
    )
    result = _enemy_turrets_down(snap)
    assert result == {"Bot": 2}


def test_enemy_turrets_down_ignores_ally_turrets() -> None:
    """TOrder turrets are ally side when active_team=ORDER → not counted."""
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TOrder_L2_P1_Base"),  # ally Top outer
        ],
    )
    assert _enemy_turrets_down(snap) == {}


def test_enemy_turrets_down_ignores_inhibitor_tier() -> None:
    """P3 = inhibitor turret; only P1/P2 count."""
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L1_P3_Base"),  # inhib — excluded
        ],
    )
    assert _enemy_turrets_down(snap) == {}


def test_enemy_turrets_down_empty_without_active_team() -> None:
    snap = _Snap(raw_events=[_turret_killed_event("Turret_TChaos_L0_P1_Base")])
    assert _enemy_turrets_down(snap) == {}


# ----------------------------------------------------------------------
# rule_lane_pressure
# ----------------------------------------------------------------------

def test_lane_pressure_fires_on_fully_open_lane() -> None:
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L0_P1_Base"),
            _turret_killed_event("Turret_TChaos_L0_P2_Base"),
        ],
    )
    rec = rule_lane_pressure(snap)
    assert rec is not None
    assert "Bot" in rec.text
    assert rec.severity == "warn"


def test_lane_pressure_fires_on_partial_open_lane() -> None:
    snap = _Snap(
        active_team="ORDER",
        raw_events=[_turret_killed_event("Turret_TChaos_L2_P1_Base")],
    )
    rec = rule_lane_pressure(snap)
    assert rec is not None
    assert "Top" in rec.text
    assert rec.severity == "info"


def test_lane_pressure_silent_when_no_turrets_down() -> None:
    snap = _Snap(active_team="ORDER", raw_events=[])
    assert rule_lane_pressure(snap) is None


def test_lane_pressure_silent_without_active_team() -> None:
    snap = _Snap(active_team="", raw_events=[_turret_killed_event("Turret_TChaos_L0_P1_Base")])
    assert rule_lane_pressure(snap) is None


# ----------------------------------------------------------------------
# rule_ace_detected
# ----------------------------------------------------------------------

def test_ace_detected_fires_when_all_five_enemies_dead() -> None:
    dead_enemies = [_Player(is_alive=False, respawn_timer=25.0) for _ in range(5)]
    five_allies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(5)]
    snap = _Snap(allies=five_allies, enemies=dead_enemies)
    rec = rule_ace_detected(snap)
    assert rec is not None
    assert "ACE" in rec.text
    assert rec.severity == "alert"
    assert rec.kind == "ace"


def test_ace_not_fires_when_one_enemy_alive() -> None:
    enemies = [_Player(is_alive=False, respawn_timer=20.0) for _ in range(4)]
    enemies.append(_Player(is_alive=True, respawn_timer=0.0))
    allies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(5)]
    snap = _Snap(allies=allies, enemies=enemies)
    assert rule_ace_detected(snap) is None


def test_ace_not_fires_with_fewer_than_five_enemies() -> None:
    """Team not fully identified → no ace."""
    dead_enemies = [_Player(is_alive=False, respawn_timer=25.0) for _ in range(3)]
    allies = [_Player(is_alive=True, respawn_timer=0.0) for _ in range(5)]
    snap = _Snap(allies=allies, enemies=dead_enemies)
    assert rule_ace_detected(snap) is None


# ----------------------------------------------------------------------
# _suppress_dominated: ace suppression
# ----------------------------------------------------------------------

def test_suppress_ace_removes_fight_and_lead_signals() -> None:
    """Rule 1 (ace): fight, gold_lead, kill_lead, numbers_adv drop."""
    recs = [
        _rec("ace", "alert"),
        _rec("fight", "alert"),
        _rec("fight_bad", "warn"),
        _rec("gold_lead", "info"),
        _rec("kill_lead", "info"),
        _rec("numbers_adv", "alert"),
    ]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert "ace" in result_kinds
    for dropped in ("fight", "fight_bad", "gold_lead", "kill_lead", "numbers_adv"):
        assert dropped not in result_kinds


def test_suppress_ace_keeps_safety_and_lane_open() -> None:
    recs = [
        _rec("ace", "alert"),
        _rec("numbers_disadv", "alert"),
        _rec("lane_open", "warn"),
    ]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert "ace" in result_kinds
    assert "lane_open" in result_kinds


# ----------------------------------------------------------------------
# rule_enemy_base_exposed — inhibitor tier tracking
# ----------------------------------------------------------------------

def test_enemy_base_exposed_fires_when_outer_inner_inhib_down() -> None:
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L0_P1_Base"),
            _turret_killed_event("Turret_TChaos_L0_P2_Base"),
            _turret_killed_event("Turret_TChaos_L0_P3_Base"),
        ],
    )
    rec = rule_enemy_base_exposed(snap)
    assert rec is not None
    assert "Bot" in rec.text
    assert rec.severity == "alert"
    assert rec.kind == "base_exposed"


def test_enemy_base_exposed_silent_when_only_two_turrets_down() -> None:
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L2_P1_Base"),
            _turret_killed_event("Turret_TChaos_L2_P2_Base"),
        ],
    )
    assert rule_enemy_base_exposed(snap) is None


def test_enemy_turrets_down_counts_inhib_when_tiers_param_includes_p3() -> None:
    snap = _Snap(
        active_team="ORDER",
        raw_events=[
            _turret_killed_event("Turret_TChaos_L1_P1_Base"),
            _turret_killed_event("Turret_TChaos_L1_P2_Base"),
            _turret_killed_event("Turret_TChaos_L1_P3_Base"),
        ],
    )
    result = _enemy_turrets_down(snap, tiers=("P1", "P2", "P3"))
    assert result == {"Mid": 3}


def test_suppress_base_exposed_absorbs_lane_open() -> None:
    recs = [_rec("base_exposed", "alert"), _rec("lane_open", "warn")]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert "base_exposed" in result_kinds
    assert "lane_open" not in result_kinds


# ----------------------------------------------------------------------
# _kill_streak — consecutive kill tracking from raw_events
# ----------------------------------------------------------------------

def _kill_event(killer: str, victim: str, t: float = 500.0) -> dict:
    return {"EventName": "ChampionKill", "KillerName": killer,
            "VictimName": victim, "EventTime": t}


def test_kill_streak_counts_consecutive_kills() -> None:
    player = _Player(champion_name="Jinx", summoner_name="P1")
    events = [
        _kill_event("Jinx", "Thresh", 300.0),
        _kill_event("Jinx", "Ashe", 310.0),
        _kill_event("Jinx", "Caitlyn", 320.0),
    ]
    assert _kill_streak(player, events) == 3


def test_kill_streak_resets_on_death() -> None:
    player = _Player(champion_name="Jinx", summoner_name="P1")
    events = [
        _kill_event("Thresh", "Jinx", 200.0),  # Jinx dies
        _kill_event("Jinx", "Ashe", 300.0),    # then gets one kill
    ]
    assert _kill_streak(player, events) == 1


def test_kill_streak_zero_with_no_kills() -> None:
    player = _Player(champion_name="Jinx", summoner_name="P1")
    events = [_kill_event("Thresh", "Ashe", 300.0)]
    assert _kill_streak(player, events) == 0


def test_kill_streak_matches_summoner_name_format() -> None:
    """KillerName may be summoner name instead of champion name."""
    player = _Player(champion_name="Jinx", summoner_name="FlashKing")
    events = [_kill_event("FlashKing", "Thresh", 300.0)]
    assert _kill_streak(player, events) == 1


def test_kill_streak_empty_events() -> None:
    player = _Player(champion_name="Jinx")
    assert _kill_streak(player, []) == 0


# ----------------------------------------------------------------------
# rule_game_ended — Win/Loss summary card
# ----------------------------------------------------------------------

def test_rule_game_ended_fires_on_win() -> None:
    snap = _Snap(game_result="Win")
    rec = rule_game_ended(snap)
    assert rec is not None
    assert "SIEG" in rec.text
    assert rec.kind == "game_end"
    assert rec.severity == "alert"


def test_rule_game_ended_fires_on_lose() -> None:
    snap = _Snap(game_result="Lose")
    rec = rule_game_ended(snap)
    assert rec is not None
    assert "NIEDERLAGE" in rec.text
    assert rec.kind == "game_end"


def test_rule_game_ended_silent_during_game() -> None:
    snap = _Snap(game_result="")
    assert rule_game_ended(snap) is None


def test_rule_game_ended_includes_drake_count() -> None:
    ally = _Player(summoner_name="P1", champion_name="Jinx")
    snap = _Snap(
        game_result="Win",
        allies=[ally],
        raw_events=[
            {"EventName": "DragonKill", "KillerName": "Jinx"},
            {"EventName": "DragonKill", "KillerName": "Jinx"},
        ],
    )
    rec = rule_game_ended(snap)
    assert rec is not None
    assert "2x Drake" in rec.text


# ----------------------------------------------------------------------
# _suppress_dominated: game_end suppresses ALL other recommendations
# ----------------------------------------------------------------------

def test_suppress_game_end_drops_all_others() -> None:
    recs = [
        _rec("game_end", "alert"),
        _rec("dragon_take", "alert"),
        _rec("fight", "alert"),
        _rec("numbers_disadv", "alert"),
        _rec("lane_open", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert len(result) == 1
    assert result[0].kind == "game_end"


# ----------------------------------------------------------------------
# Herald tracking — _enemy_herald_pickup + rule_enemy_herald_danger
# ----------------------------------------------------------------------

def _herald_kill_event(killer: str, event_time: float = 500.0) -> dict:
    return {"EventName": "HeraldKill", "KillerName": killer, "EventTime": event_time}


def test_enemy_herald_pickup_returns_remaining_when_enemy_took_herald() -> None:
    enemy = _Player(summoner_name="EnemyP", champion_name="Vi")
    snap = _Snap(
        game_time=550.0,
        enemies=[enemy],
        raw_events=[_herald_kill_event("Vi", event_time=500.0)],
    )
    result = _enemy_herald_pickup(snap)
    assert result is not None
    pickup_t, remaining = result
    assert pickup_t == 500.0
    assert abs(remaining - (HERALD_USAGE_WINDOW_S - 50.0)) < 0.5


def test_enemy_herald_pickup_returns_none_when_ally_took_herald() -> None:
    ally = _Player(summoner_name="Me", champion_name="Jarvan IV")
    enemy = _Player(summoner_name="EnemyP", champion_name="Vi")
    snap = _Snap(
        game_time=550.0,
        allies=[ally],
        enemies=[enemy],
        raw_events=[_herald_kill_event("Jarvan IV", event_time=500.0)],
    )
    assert _enemy_herald_pickup(snap) is None


def test_enemy_herald_pickup_returns_none_after_window_expires() -> None:
    enemy = _Player(champion_name="Vi")
    snap = _Snap(
        game_time=700.0,  # 200s after pickup — past HERALD_USAGE_WINDOW_S=180
        enemies=[enemy],
        raw_events=[_herald_kill_event("Vi", event_time=500.0)],
    )
    assert _enemy_herald_pickup(snap) is None


def test_rule_enemy_herald_danger_fires_within_window() -> None:
    enemy = _Player(champion_name="Hecarim", summoner_name="EnemyJG")
    snap = _Snap(
        game_time=560.0,
        enemies=[enemy],
        raw_events=[_herald_kill_event("Hecarim", event_time=500.0)],
    )
    rec = rule_enemy_herald_danger(snap)
    assert rec is not None
    assert "Herald" in rec.text
    assert rec.kind == "enemy_herald"


def test_rule_enemy_herald_danger_silent_when_no_herald() -> None:
    snap = _Snap(enemies=[_Player(champion_name="Vi")], raw_events=[])
    assert rule_enemy_herald_danger(snap) is None


# ----------------------------------------------------------------------
# Inhibitor tracking — _active_enemy_inhibitors_down + rule
# ----------------------------------------------------------------------

def _inhib_killed_event(killer: str) -> dict:
    return {"EventName": "InhibitorKilled", "KillerName": killer, "EventTime": 1800.0}


def _inhib_respawned_event() -> dict:
    return {"EventName": "InhibitorRespawned", "EventTime": 2300.0}


def test_active_enemy_inhibitors_down_counts_ally_kills() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Carry")
    snap = _Snap(
        allies=[ally],
        raw_events=[
            _inhib_killed_event("Jinx"),
            _inhib_killed_event("Jinx"),
        ],
    )
    assert _active_enemy_inhibitors_down(snap) == 2


def test_active_enemy_inhibitors_down_subtracts_respawns() -> None:
    ally = _Player(champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        raw_events=[
            _inhib_killed_event("Jinx"),
            _inhib_respawned_event(),
        ],
    )
    assert _active_enemy_inhibitors_down(snap) == 0


def test_active_enemy_inhibitors_down_ignores_enemy_kills() -> None:
    """Enemy killing OUR inhibitor — not counted."""
    ally = _Player(champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        raw_events=[_inhib_killed_event("Vi")],  # Vi is not in allies
    )
    assert _active_enemy_inhibitors_down(snap) == 0


def test_rule_enemy_inhibitor_down_fires_when_active() -> None:
    ally = _Player(champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        raw_events=[_inhib_killed_event("Jinx")],
    )
    rec = rule_enemy_inhibitor_down(snap)
    assert rec is not None
    assert "Inhib" in rec.text
    assert rec.kind == "inhib_down"


def test_rule_enemy_inhibitor_down_silent_when_respawned() -> None:
    ally = _Player(champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        raw_events=[_inhib_killed_event("Jinx"), _inhib_respawned_event()],
    )
    assert rule_enemy_inhibitor_down(snap) is None


# ----------------------------------------------------------------------
# _active_ally_inhibitors_down + rule_ally_inhib_down (B4 risk signal)
# ----------------------------------------------------------------------

def test_active_ally_inhibitors_down_counts_enemy_kills() -> None:
    """Enemy (Draven) killed our inhibitor → count = 1."""
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        enemies=[enemy],
        raw_events=[_inhib_killed_event("Draven")],
    )
    assert _active_ally_inhibitors_down(snap) == 1


def test_active_ally_inhibitors_down_zero_when_respawned() -> None:
    enemy = _Player(champion_name="Draven")
    snap = _Snap(
        enemies=[enemy],
        raw_events=[_inhib_killed_event("Draven"), _inhib_respawned_event()],
    )
    assert _active_ally_inhibitors_down(snap) == 0


def test_active_ally_inhibitors_down_zero_when_ally_killed_it() -> None:
    """Ally kills enemy inhib → should NOT count as our inhib down."""
    ally = _Player(champion_name="Jinx")
    snap = _Snap(
        allies=[ally],
        enemies=[_Player(champion_name="Draven")],
        raw_events=[_inhib_killed_event("Jinx")],
    )
    assert _active_ally_inhibitors_down(snap) == 0


def test_rule_ally_inhib_down_fires_when_enemy_destroyed_our_inhib() -> None:
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        enemies=[enemy],
        raw_events=[_inhib_killed_event("Draven")],
    )
    rec = rule_ally_inhib_down(snap)
    assert rec is not None
    assert rec.kind == "ally_inhib_down"
    assert rec.severity == "warn"
    assert rec.category == "safety"


def test_rule_ally_inhib_down_alert_when_multiple() -> None:
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        enemies=[enemy],
        raw_events=[_inhib_killed_event("Draven"), _inhib_killed_event("Draven")],
    )
    rec = rule_ally_inhib_down(snap)
    assert rec is not None
    assert rec.severity == "alert"


def test_rule_ally_inhib_down_silent_when_none() -> None:
    snap = _Snap(enemies=[_Player(champion_name="Draven")])
    assert rule_ally_inhib_down(snap) is None


def test_suppress_ally_inhib_down_removes_obj_take() -> None:
    """When our inhib is down, dragon/baron take and lane_open are suppressed."""
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("dragon_take", "alert"),
        _rec("baron_take", "alert"),
        _rec("lane_open", "info"),
        _rec("fight", "alert"),  # fight should survive
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "ally_inhib_down" in kinds
    assert "dragon_take" not in kinds
    assert "baron_take" not in kinds
    assert "lane_open" not in kinds
    assert "fight" in kinds


# ----------------------------------------------------------------------
# Suppression: inhib_down supersedes base_exposed + lane_open
# ----------------------------------------------------------------------

def test_suppress_inhib_down_removes_base_exposed_and_lane_open() -> None:
    recs = [
        _rec("inhib_down", "warn"),
        _rec("base_exposed", "alert"),
        _rec("lane_open", "info"),
        _rec("fight", "alert"),
    ]
    result = _suppress_dominated(recs)
    result_kinds = {r.kind for r in result}
    assert "inhib_down" in result_kinds
    assert "base_exposed" not in result_kinds
    assert "lane_open" not in result_kinds
    assert "fight" in result_kinds  # fight stays


# ----------------------------------------------------------------------
# rule_enemy_flash_down + evaluate(spell_tracker=...)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    FLASH_DOWN_ALERT_S,
    rule_enemy_flash_down,
)
from champ_assistant.lcda.spell_tracker import SpellTracker
from champ_assistant.lcda.players import LiveSummonerSpell


@dataclass
class _PlayerWithSpells:
    summoner_name: str = "Enemy"
    champion_name: str = "Darius"
    spell_one: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Flash", cooldown=300.0)
    )
    spell_two: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Ignite", cooldown=180.0)
    )
    is_alive: bool = True
    respawn_timer: float = 0.0


def _tracker_with_flash(summoner: str, game_time: float, remaining: float) -> SpellTracker:
    """Build a SpellTracker with one Flash entry whose remaining() == remaining."""
    t = SpellTracker()
    cast_at = game_time - (300.0 - remaining)
    t.mark_used(summoner, "Flash", 300.0, cast_at)
    return t


def test_flash_down_fires_when_enemy_flash_on_cd() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithSpells(summoner_name="Jinx", champion_name="Jinx")],
    )
    tracker = _tracker_with_flash("Jinx", 600.0, FLASH_DOWN_ALERT_S + 30.0)
    rec = rule_enemy_flash_down(snap, tracker)
    assert rec is not None
    assert rec.kind == "flash_down"
    assert "Jinx" in rec.text
    assert rec.severity == "warn"


def test_flash_down_silent_when_flash_almost_ready() -> None:
    """Flash with only 30s remaining (< FLASH_DOWN_ALERT_S) — no alert."""
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithSpells(summoner_name="Darius")],
    )
    tracker = _tracker_with_flash("Darius", 600.0, FLASH_DOWN_ALERT_S - 30.0)
    assert rule_enemy_flash_down(snap, tracker) is None


def test_flash_down_silent_when_tracker_empty() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithSpells(summoner_name="Garen")],
    )
    assert rule_enemy_flash_down(snap, SpellTracker()) is None


def test_flash_down_silent_when_enemy_has_no_flash() -> None:
    no_flash_player = _PlayerWithSpells(
        summoner_name="Singed",
        spell_one=LiveSummonerSpell(name="Ghost", cooldown=210.0),
        spell_two=LiveSummonerSpell(name="Ignite", cooldown=180.0),
    )
    snap = _Snap(game_time=600.0, enemies=[no_flash_player])
    tracker = SpellTracker()
    tracker.mark_used("Singed", "Ghost", 210.0, 500.0)
    assert rule_enemy_flash_down(snap, tracker) is None


def test_flash_down_multiple_enemies_in_text() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[
            _PlayerWithSpells(summoner_name="A", champion_name="Darius"),
            _PlayerWithSpells(summoner_name="B", champion_name="Garen"),
        ],
    )
    # Cast both flashes recently enough that remaining > FLASH_DOWN_ALERT_S
    cast_at = 600.0 - (300.0 - (FLASH_DOWN_ALERT_S + 30.0))
    tracker = SpellTracker()
    tracker.mark_used("A", "Flash", 300.0, cast_at)
    tracker.mark_used("B", "Flash", 300.0, cast_at - 10.0)
    rec = rule_enemy_flash_down(snap, tracker)
    assert rec is not None
    assert "2×" in rec.text or "2" in rec.text


def test_evaluate_includes_flash_down_when_tracker_provided() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithSpells(summoner_name="Jinx")],
    )
    tracker = _tracker_with_flash("Jinx", 600.0, FLASH_DOWN_ALERT_S + 60.0)
    recs = evaluate(snap, spell_tracker=tracker)
    assert any(r.kind == "flash_down" for r in recs)


def test_evaluate_no_flash_down_without_tracker() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithSpells(summoner_name="Jinx")],
    )
    recs = evaluate(snap)
    assert not any(r.kind == "flash_down" for r in recs)


def test_flash_down_suppressed_by_numbers_disadv() -> None:
    """Flash-down engage window must not show when a teammate is dead."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("flash_down", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "flash_down" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


# ----------------------------------------------------------------------
# rule_baron_buff_expiring (B4 — Hand-of-Baron push reminder)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    BARON_BUFF_DURATION_S,
    BARON_BUFF_EXPIRY_ALERT_S,
    _ally_baron_buff_remaining,
    rule_baron_buff_expiring,
)


def _baron_kill_event(killer: str, event_time: float) -> dict:
    return {"EventName": "BaronKill", "KillerName": killer, "EventTime": event_time}


def test_ally_baron_buff_remaining_none_when_no_events() -> None:
    snap = _Snap(allies=[_Player(champion_name="Jinx")], raw_events=[])
    assert _ally_baron_buff_remaining(snap) is None


def test_ally_baron_buff_remaining_none_when_no_baron_kill() -> None:
    snap = _Snap(
        allies=[_Player(champion_name="Jinx")],
        raw_events=[{"EventName": "DragonKill", "KillerName": "Jinx", "EventTime": 500.0}],
    )
    assert _ally_baron_buff_remaining(snap) is None


def test_ally_baron_buff_remaining_returns_correct_seconds() -> None:
    """Ally killed baron at 400s, game_time=500s → 80s remaining (180-100)."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", 400.0)],
    )
    assert _ally_baron_buff_remaining(snap) == pytest.approx(80.0)


def test_ally_baron_buff_remaining_none_when_expired() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=700.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", 400.0)],
    )
    assert _ally_baron_buff_remaining(snap) is None


def test_ally_baron_buff_remaining_none_when_enemy_killed_baron() -> None:
    """Enemy baron kill must not be counted as ally buff."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        enemies=[_Player(champion_name="Draven", summoner_name="Draven")],
        raw_events=[_baron_kill_event("Draven", 400.0)],
    )
    assert _ally_baron_buff_remaining(snap) is None


def test_rule_baron_buff_expiring_fires_with_warn_in_alert_window() -> None:
    """50s remaining → severity=warn."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 50.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", kill_time)],
    )
    rec = rule_baron_buff_expiring(snap)
    assert rec is not None
    assert rec.kind == "baron_buff_expiring"
    assert rec.severity == "warn"
    assert "pushen" in rec.text.lower()


def test_rule_baron_buff_expiring_alert_when_very_close() -> None:
    """20s remaining → severity=alert."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 20.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", kill_time)],
    )
    rec = rule_baron_buff_expiring(snap)
    assert rec is not None
    assert rec.severity == "alert"


def test_rule_baron_buff_expiring_silent_when_plenty_of_time() -> None:
    """120s remaining → still well within buff, no alert yet."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 120.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", kill_time)],
    )
    assert rule_baron_buff_expiring(snap) is None


def test_rule_baron_buff_expiring_silent_when_no_baron_kill() -> None:
    snap = _Snap(allies=[_Player(champion_name="Jinx")])
    assert rule_baron_buff_expiring(snap) is None


def test_baron_buff_expiring_in_all_rules() -> None:
    """rule_baron_buff_expiring must be reachable through evaluate()."""
    import pytest as _pytest
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "baron_buff_expiring" for r in recs)


def test_baron_buff_expiring_suppressed_by_numbers_disadv() -> None:
    """Pushing while short-handed is bad — buff expiry must not override safety."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("baron_buff_expiring", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "baron_buff_expiring" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


# ----------------------------------------------------------------------
# rule_enemy_baron_buff + rule_elder_buff_expiring (B4)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    ELDER_BUFF_DURATION_S,
    ELDER_BUFF_EXPIRY_ALERT_S,
    _ally_elder_buff_remaining,
    _enemy_baron_buff_remaining,
    rule_elder_buff_expiring,
    rule_enemy_baron_buff,
)


def _elder_kill_event(killer: str, event_time: float) -> dict:
    return {
        "EventName": "DragonKill",
        "KillerName": killer,
        "EventTime": event_time,
        "DragonType": "Elder",
    }


# --- _enemy_baron_buff_remaining ---

def test_enemy_baron_buff_remaining_none_when_no_enemy_baron() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        enemies=[_Player(champion_name="Draven", summoner_name="Draven")],
        raw_events=[_baron_kill_event("Jinx", 400.0)],
    )
    assert _enemy_baron_buff_remaining(snap) is None


def test_enemy_baron_buff_remaining_returns_correct_seconds() -> None:
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=500.0,
        enemies=[enemy],
        raw_events=[_baron_kill_event("Draven", 400.0)],
    )
    assert _enemy_baron_buff_remaining(snap) == pytest.approx(80.0)


def test_enemy_baron_buff_remaining_none_when_expired() -> None:
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=700.0,
        enemies=[enemy],
        raw_events=[_baron_kill_event("Draven", 400.0)],
    )
    assert _enemy_baron_buff_remaining(snap) is None


# --- rule_enemy_baron_buff ---

def test_rule_enemy_baron_buff_fires_warn_when_plenty_of_time() -> None:
    """120s remaining → warn, category=safety."""
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 120.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_baron_kill_event("Draven", kill_time)],
    )
    rec = rule_enemy_baron_buff(snap)
    assert rec is not None
    assert rec.kind == "enemy_baron_buff"
    assert rec.severity == "warn"
    assert rec.category == "safety"
    assert "Basis" in rec.text or "sichern" in rec.text.lower()


def test_rule_enemy_baron_buff_fires_alert_when_expiring() -> None:
    """30s remaining → alert, category=tempo (counter-engage window)."""
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_baron_kill_event("Draven", kill_time)],
    )
    rec = rule_enemy_baron_buff(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.category == "tempo"


def test_rule_enemy_baron_buff_silent_when_no_enemy_baron() -> None:
    snap = _Snap(enemies=[_Player(champion_name="Draven")])
    assert rule_enemy_baron_buff(snap) is None


def test_enemy_baron_buff_survives_numbers_disadv() -> None:
    """Defensive info (enemy has baron) must NOT be suppressed when short-handed."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("enemy_baron_buff", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert any(r.kind == "enemy_baron_buff" for r in result)


def test_enemy_baron_buff_in_all_rules() -> None:
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    kill_time = 600.0 - (BARON_BUFF_DURATION_S - 90.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_baron_kill_event("Draven", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "enemy_baron_buff" for r in recs)


# --- _ally_elder_buff_remaining ---

def test_ally_elder_buff_remaining_none_when_no_elder_kill() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[_baron_kill_event("Jinx", 400.0)],
    )
    assert _ally_elder_buff_remaining(snap) is None


def test_ally_elder_buff_remaining_returns_correct_seconds() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", 400.0)],
    )
    assert _ally_elder_buff_remaining(snap) == pytest.approx(50.0)


def test_ally_elder_buff_remaining_none_when_expired() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=700.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", 400.0)],
    )
    assert _ally_elder_buff_remaining(snap) is None


def test_ally_elder_buff_remaining_ignores_non_elder_dragons() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[{
            "EventName": "DragonKill", "KillerName": "Jinx",
            "EventTime": 400.0, "DragonType": "Fire",
        }],
    )
    assert _ally_elder_buff_remaining(snap) is None


def test_ally_elder_buff_remaining_uses_trap_type_fallback() -> None:
    """Some LCDA versions use TrapType instead of DragonType."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[{
            "EventName": "DragonKill", "KillerName": "Jinx",
            "EventTime": 400.0, "TrapType": "Elder",
        }],
    )
    assert _ally_elder_buff_remaining(snap) == pytest.approx(50.0)


# --- rule_elder_buff_expiring ---

def test_rule_elder_buff_expiring_fires_warn_at_50s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 50.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", kill_time)],
    )
    rec = rule_elder_buff_expiring(snap)
    assert rec is not None
    assert rec.kind == "elder_buff_expiring"
    assert rec.severity == "warn"
    assert "teamfight" in rec.text.lower() or "elder" in rec.text.lower()


def test_rule_elder_buff_expiring_alert_at_20s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 20.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", kill_time)],
    )
    rec = rule_elder_buff_expiring(snap)
    assert rec is not None
    assert rec.severity == "alert"


def test_rule_elder_buff_expiring_silent_when_plenty_of_time() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 100.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", kill_time)],
    )
    assert rule_elder_buff_expiring(snap) is None


def test_rule_elder_buff_expiring_silent_when_no_elder() -> None:
    snap = _Snap(allies=[_Player(champion_name="Jinx")])
    assert rule_elder_buff_expiring(snap) is None


def test_elder_buff_expiring_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("elder_buff_expiring", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "elder_buff_expiring" for r in result)


def test_elder_buff_expiring_in_all_rules() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_elder_kill_event("Jinx", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "elder_buff_expiring" for r in recs)


# ----------------------------------------------------------------------
# rule_ally_herald_window (B4 — Eye of the Herald placement reminder)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    HERALD_USAGE_WINDOW_S,
    _herald_pickup,
    rule_ally_herald_window,
)


def _herald_event(killer: str, event_time: float) -> dict:
    return {"EventName": "HeraldKill", "KillerName": killer, "EventTime": event_time}


def test_herald_pickup_ally_returns_remaining_when_active() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", 400.0)],
    )
    result = _herald_pickup(snap, team="ally")
    assert result is not None
    _, remaining = result
    assert remaining == pytest.approx(HERALD_USAGE_WINDOW_S - 100.0)


def test_herald_pickup_ally_none_when_enemy_took_it() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=500.0,
        allies=[ally],
        enemies=[enemy],
        raw_events=[_herald_event("Draven", 400.0)],
    )
    assert _herald_pickup(snap, team="ally") is None


def test_herald_pickup_ally_none_when_expired() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=700.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", 400.0)],
    )
    assert _herald_pickup(snap, team="ally") is None


def test_rule_ally_herald_window_info_when_plenty_of_time() -> None:
    """150s remaining → info severity, category=tempo."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    pickup_t = 600.0 - (HERALD_USAGE_WINDOW_S - 150.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", pickup_t)],
    )
    rec = rule_ally_herald_window(snap)
    assert rec is not None
    assert rec.kind == "ally_herald"
    assert rec.severity == "info"
    assert rec.category == "tempo"


def test_rule_ally_herald_window_warn_at_90s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    pickup_t = 600.0 - (HERALD_USAGE_WINDOW_S - 90.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", pickup_t)],
    )
    rec = rule_ally_herald_window(snap)
    assert rec is not None
    assert rec.severity == "warn"


def test_rule_ally_herald_window_alert_at_45s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    pickup_t = 600.0 - (HERALD_USAGE_WINDOW_S - 45.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", pickup_t)],
    )
    rec = rule_ally_herald_window(snap)
    assert rec is not None
    assert rec.severity == "alert"


def test_rule_ally_herald_window_silent_when_no_herald() -> None:
    snap = _Snap(allies=[_Player(champion_name="Jinx")])
    assert rule_ally_herald_window(snap) is None


def test_rule_ally_herald_window_silent_when_expired() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=800.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", 400.0)],
    )
    assert rule_ally_herald_window(snap) is None


def test_ally_herald_in_all_rules() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    pickup_t = 600.0 - (HERALD_USAGE_WINDOW_S - 90.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_herald_event("Jinx", pickup_t)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "ally_herald" for r in recs)


def test_ally_herald_suppressed_by_numbers_disadv() -> None:
    """Don't split to place herald while a teammate is dead."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("ally_herald", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "ally_herald" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


def test_enemy_herald_danger_still_works_after_refactor() -> None:
    """_enemy_herald_pickup must continue working after _herald_pickup refactor."""
    from champ_assistant.advisor.decision_engine import _enemy_herald_pickup
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=500.0,
        enemies=[enemy],
        raw_events=[_herald_event("Draven", 400.0)],
    )
    result = _enemy_herald_pickup(snap)
    assert result is not None
    _, remaining = result
    assert remaining == pytest.approx(HERALD_USAGE_WINDOW_S - 100.0)


# ----------------------------------------------------------------------
# rule_enemy_tp_down (B2 — Teleport cooldown tracking)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    TP_DOWN_ALERT_S,
    rule_enemy_tp_down,
)


@dataclass
class _PlayerWithTP:
    summoner_name: str = "Enemy"
    champion_name: str = "Darius"
    spell_one: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Teleport", cooldown=210.0)
    )
    spell_two: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Flash", cooldown=300.0)
    )
    is_alive: bool = True
    respawn_timer: float = 0.0


def _tracker_with_tp(summoner: str, game_time: float, remaining: float) -> SpellTracker:
    t = SpellTracker()
    cast_at = game_time - (210.0 - remaining)
    t.mark_used(summoner, "Teleport", 210.0, cast_at)
    return t


def test_tp_down_fires_info_for_single_enemy() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithTP(summoner_name="Garen", champion_name="Garen")],
    )
    tracker = _tracker_with_tp("Garen", 600.0, TP_DOWN_ALERT_S + 30.0)
    rec = rule_enemy_tp_down(snap, tracker)
    assert rec is not None
    assert rec.kind == "tp_down"
    assert rec.severity == "info"
    assert "Garen" in rec.text or "TP" in rec.text


def test_tp_down_fires_warn_for_multiple_enemies() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[
            _PlayerWithTP(summoner_name="A", champion_name="Garen"),
            _PlayerWithTP(summoner_name="B", champion_name="Darius"),
        ],
    )
    cast_at = 600.0 - (210.0 - (TP_DOWN_ALERT_S + 30.0))
    tracker = SpellTracker()
    tracker.mark_used("A", "Teleport", 210.0, cast_at)
    tracker.mark_used("B", "Teleport", 210.0, cast_at - 10.0)
    rec = rule_enemy_tp_down(snap, tracker)
    assert rec is not None
    assert rec.severity == "warn"
    assert "2" in rec.text


def test_tp_down_silent_when_tp_almost_ready() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithTP(summoner_name="Garen")],
    )
    tracker = _tracker_with_tp("Garen", 600.0, TP_DOWN_ALERT_S - 30.0)
    assert rule_enemy_tp_down(snap, tracker) is None


def test_tp_down_silent_when_tracker_empty() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithTP(summoner_name="Garen")],
    )
    assert rule_enemy_tp_down(snap, SpellTracker()) is None


def test_tp_down_silent_when_enemy_has_no_tp() -> None:
    no_tp = _PlayerWithTP(
        summoner_name="Zed",
        spell_one=LiveSummonerSpell(name="Ignite", cooldown=180.0),
        spell_two=LiveSummonerSpell(name="Flash", cooldown=300.0),
    )
    snap = _Snap(game_time=600.0, enemies=[no_tp])
    tracker = SpellTracker()
    tracker.mark_used("Zed", "Ignite", 180.0, 500.0)
    assert rule_enemy_tp_down(snap, tracker) is None


def test_evaluate_includes_tp_down_when_tracker_provided() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithTP(summoner_name="Garen")],
    )
    tracker = _tracker_with_tp("Garen", 600.0, TP_DOWN_ALERT_S + 60.0)
    recs = evaluate(snap, spell_tracker=tracker)
    assert any(r.kind == "tp_down" for r in recs)


def test_evaluate_no_tp_down_without_tracker() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithTP(summoner_name="Garen")],
    )
    recs = evaluate(snap)
    assert not any(r.kind == "tp_down" for r in recs)


def test_tp_down_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("tp_down", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "tp_down" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


def test_flash_down_and_tp_down_both_appear_together() -> None:
    """Both rules can coexist — different tactical meaning."""
    snap = _Snap(
        game_time=600.0,
        enemies=[
            _PlayerWithSpells(summoner_name="A"),   # has Flash
            _PlayerWithTP(summoner_name="B"),        # has TP
        ],
    )
    flash_cast = 600.0 - (300.0 - (FLASH_DOWN_ALERT_S + 30.0))
    tp_cast = 600.0 - (210.0 - (TP_DOWN_ALERT_S + 30.0))
    tracker = SpellTracker()
    tracker.mark_used("A", "Flash", 300.0, flash_cast)
    tracker.mark_used("B", "Teleport", 210.0, tp_cast)
    recs = evaluate(snap, spell_tracker=tracker)
    kinds = {r.kind for r in recs}
    assert "flash_down" in kinds
    assert "tp_down" in kinds


# ----------------------------------------------------------------------
# rule_enemy_combat_spell_down (B2 — Exhaust/Heal/Ignite/Barrier/Cleanse)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    COMBAT_SPELL_ALERT_S,
    rule_enemy_combat_spell_down,
)


@dataclass
class _PlayerWithExhaust:
    summoner_name: str = "Support"
    champion_name: str = "Thresh"
    spell_one: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Exhaust", cooldown=210.0)
    )
    spell_two: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Flash", cooldown=300.0)
    )
    is_alive: bool = True
    respawn_timer: float = 0.0


def _tracker_with_combat_spell(
    summoner: str, spell: str, cooldown: float, game_time: float, remaining: float
) -> SpellTracker:
    t = SpellTracker()
    cast_at = game_time - (cooldown - remaining)
    t.mark_used(summoner, spell, cooldown, cast_at)
    return t


def test_combat_spell_down_fires_for_exhaust() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithExhaust(summoner_name="Thresh")],
    )
    tracker = _tracker_with_combat_spell(
        "Thresh", "Exhaust", 210.0, 600.0, COMBAT_SPELL_ALERT_S + 30.0
    )
    rec = rule_enemy_combat_spell_down(snap, tracker)
    assert rec is not None
    assert rec.kind == "combat_spell_down"
    assert rec.severity == "info"
    assert "Exhaust" in rec.text


def test_combat_spell_down_fires_for_heal() -> None:
    heal_player = _PlayerWithExhaust(
        summoner_name="ADC",
        champion_name="Jinx",
        spell_one=LiveSummonerSpell(name="Heal", cooldown=240.0),
        spell_two=LiveSummonerSpell(name="Flash", cooldown=300.0),
    )
    snap = _Snap(game_time=600.0, enemies=[heal_player])
    tracker = _tracker_with_combat_spell(
        "ADC", "Heal", 240.0, 600.0, COMBAT_SPELL_ALERT_S + 30.0
    )
    rec = rule_enemy_combat_spell_down(snap, tracker)
    assert rec is not None
    assert "Heal" in rec.text


def test_combat_spell_down_fires_for_ignite() -> None:
    ignite_player = _PlayerWithExhaust(
        summoner_name="Mid",
        champion_name="Zed",
        spell_one=LiveSummonerSpell(name="Ignite", cooldown=180.0),
        spell_two=LiveSummonerSpell(name="Flash", cooldown=300.0),
    )
    snap = _Snap(game_time=600.0, enemies=[ignite_player])
    tracker = _tracker_with_combat_spell(
        "Mid", "Ignite", 180.0, 600.0, COMBAT_SPELL_ALERT_S + 30.0
    )
    rec = rule_enemy_combat_spell_down(snap, tracker)
    assert rec is not None
    assert "Ignite" in rec.text


def test_combat_spell_down_silent_when_almost_ready() -> None:
    snap = _Snap(game_time=600.0, enemies=[_PlayerWithExhaust(summoner_name="Thresh")])
    tracker = _tracker_with_combat_spell(
        "Thresh", "Exhaust", 210.0, 600.0, COMBAT_SPELL_ALERT_S - 20.0
    )
    assert rule_enemy_combat_spell_down(snap, tracker) is None


def test_combat_spell_down_silent_when_tracker_empty() -> None:
    snap = _Snap(game_time=600.0, enemies=[_PlayerWithExhaust()])
    assert rule_enemy_combat_spell_down(snap, SpellTracker()) is None


def test_combat_spell_down_silent_for_flash_and_tp() -> None:
    """Flash and TP have their own rules — must NOT appear in combat_spell_down."""
    flash_player = _PlayerWithSpells(summoner_name="A")  # has Flash
    tp_player = _PlayerWithTP(summoner_name="B")          # has TP
    snap = _Snap(game_time=600.0, enemies=[flash_player, tp_player])
    cast = 600.0 - (300.0 - (FLASH_DOWN_ALERT_S + 30.0))
    tracker = SpellTracker()
    tracker.mark_used("A", "Flash", 300.0, cast)
    tracker.mark_used("B", "Teleport", 210.0, cast)
    rec = rule_enemy_combat_spell_down(snap, tracker)
    assert rec is None


def test_combat_spell_down_multi_groups_into_one_card() -> None:
    p1 = _PlayerWithExhaust(summoner_name="S1")
    p2 = _PlayerWithExhaust(summoner_name="S2",
                             spell_one=LiveSummonerSpell(name="Ignite", cooldown=180.0))
    snap = _Snap(game_time=600.0, enemies=[p1, p2])
    t = SpellTracker()
    cast = 600.0 - (210.0 - (COMBAT_SPELL_ALERT_S + 30.0))
    t.mark_used("S1", "Exhaust", 210.0, cast)
    t.mark_used("S2", "Ignite", 180.0, cast)
    rec = rule_enemy_combat_spell_down(snap, t)
    assert rec is not None
    assert rec.kind == "combat_spell_down"


def test_evaluate_includes_combat_spell_down_with_tracker() -> None:
    snap = _Snap(
        game_time=600.0,
        enemies=[_PlayerWithExhaust(summoner_name="Thresh")],
    )
    tracker = _tracker_with_combat_spell(
        "Thresh", "Exhaust", 210.0, 600.0, COMBAT_SPELL_ALERT_S + 60.0
    )
    recs = evaluate(snap, spell_tracker=tracker)
    assert any(r.kind == "combat_spell_down" for r in recs)


def test_combat_spell_down_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("combat_spell_down", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "combat_spell_down" for r in result)


# ----------------------------------------------------------------------
# rule_enemy_inhib_expiring (B4 — enemy inhibitor respawn countdown)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    INHIB_EXPIRY_ALERT_S,
    INHIB_RESPAWN_S,
    _earliest_enemy_inhib_respawn_remaining,
    rule_enemy_inhib_expiring,
)


def _ally_inhib_kill_event(killer: str, event_time: float) -> dict:
    return {"EventName": "InhibitorKilled", "KillerName": killer, "EventTime": event_time}


def _inhib_respawned_event() -> dict:
    return {"EventName": "InhibitorRespawned"}


# --- _earliest_enemy_inhib_respawn_remaining ---

def test_inhib_respawn_remaining_none_when_no_kills() -> None:
    snap = _Snap(allies=[_Player(champion_name="Jinx")], raw_events=[])
    assert _earliest_enemy_inhib_respawn_remaining(snap) is None


def test_inhib_respawn_remaining_returns_correct_seconds() -> None:
    """Ally killed inhib at t=400, game_time=600 → 100s remaining (300-200)."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_ally_inhib_kill_event("Jinx", 400.0)],
    )
    assert _earliest_enemy_inhib_respawn_remaining(snap) == pytest.approx(100.0)


def test_inhib_respawn_remaining_none_when_already_respawned() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=750.0,
        allies=[ally],
        raw_events=[
            _ally_inhib_kill_event("Jinx", 400.0),
            _inhib_respawned_event(),
        ],
    )
    assert _earliest_enemy_inhib_respawn_remaining(snap) is None


def test_inhib_respawn_remaining_returns_nearest_when_two_active() -> None:
    """Two inhib kills, neither respawned yet → return the one closer to respawn."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=700.0,
        allies=[ally],
        raw_events=[
            _ally_inhib_kill_event("Jinx", 440.0),  # respawns at 740 → 40s left
            _ally_inhib_kill_event("Jinx", 490.0),  # respawns at 790 → 90s left
        ],
    )
    result = _earliest_enemy_inhib_respawn_remaining(snap)
    assert result == pytest.approx(40.0)


def test_inhib_respawn_remaining_skips_enemy_kills() -> None:
    """Enemy killing OUR inhib must NOT count."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        raw_events=[_ally_inhib_kill_event("Draven", 400.0)],
    )
    assert _earliest_enemy_inhib_respawn_remaining(snap) is None


# --- rule_enemy_inhib_expiring ---

def test_rule_enemy_inhib_expiring_fires_warn_at_50s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (INHIB_RESPAWN_S - 50.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_ally_inhib_kill_event("Jinx", kill_time)],
    )
    rec = rule_enemy_inhib_expiring(snap)
    assert rec is not None
    assert rec.kind == "inhib_expiring"
    assert rec.severity == "warn"
    assert "Nexus" in rec.text or "respawnt" in rec.text.lower()


def test_rule_enemy_inhib_expiring_alert_at_20s() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (INHIB_RESPAWN_S - 20.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_ally_inhib_kill_event("Jinx", kill_time)],
    )
    rec = rule_enemy_inhib_expiring(snap)
    assert rec is not None
    assert rec.severity == "alert"


def test_rule_enemy_inhib_expiring_silent_when_plenty_of_time() -> None:
    """120s remaining → still well within respawn window, no alert yet."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (INHIB_RESPAWN_S - 120.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_ally_inhib_kill_event("Jinx", kill_time)],
    )
    assert rule_enemy_inhib_expiring(snap) is None


def test_rule_enemy_inhib_expiring_silent_after_respawn() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=750.0,
        allies=[ally],
        raw_events=[
            _ally_inhib_kill_event("Jinx", 400.0),
            _inhib_respawned_event(),
        ],
    )
    assert rule_enemy_inhib_expiring(snap) is None


def test_inhib_expiring_in_all_rules() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    kill_time = 600.0 - (INHIB_RESPAWN_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        raw_events=[_ally_inhib_kill_event("Jinx", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "inhib_expiring" for r in recs)


def test_inhib_expiring_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("inhib_expiring", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "inhib_expiring" for r in result)


def test_inhib_expiring_suppressed_by_ally_inhib_down() -> None:
    """Defend our base first — don't push theirs while super-minions flood ours."""
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("inhib_expiring", "warn"),
        _rec("dragon_take", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "inhib_expiring" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


# ----------------------------------------------------------------------
# rule_elder_window (B1 — Elder Dragon handling)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    BARON_SETUP_WINDOW_S,
    rule_elder_window,
)


def _elder_drake_in(seconds: float) -> _Objective:
    return _Objective(name="Dragon", next_spawn=600.0 + seconds, last_killed=300.0, detail="Elder")


def _four_dragon_kills(killer: str) -> list[dict]:
    return [
        {"EventName": "DragonKill", "KillerName": killer, "EventTime": t}
        for t in [300.0, 360.0, 420.0, 480.0]
    ]


def test_elder_window_silent_when_no_dragon_objective() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(game_time=600.0, allies=[ally])
    assert rule_elder_window(snap) is None


def test_elder_window_silent_when_regular_dragon() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[_Player()],
        objectives=[_drake_in(20)],  # no detail == "Elder"
    )
    assert rule_elder_window(snap) is None


def test_elder_window_silent_when_too_far_away() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    obj = _elder_drake_in(BARON_SETUP_WINDOW_S + 60)
    snap = _Snap(game_time=600.0, allies=[ally], enemies=[_Player()], objectives=[obj])
    assert rule_elder_window(snap) is None


def test_elder_window_fires_alert_when_ally_has_soul() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        objectives=[_elder_drake_in(30)],
        raw_events=_four_dragon_kills("Jinx"),
    )
    rec = rule_elder_window(snap)
    assert rec is not None
    assert rec.kind == "elder_take"
    assert "Soul" in rec.text or "GG" in rec.text


def test_elder_window_alert_when_enemy_has_soul() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    enemy_kills = [
        {"EventName": "DragonKill", "KillerName": "Draven", "EventTime": t}
        for t in [300.0, 360.0, 420.0, 480.0]
    ]
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        objectives=[_elder_drake_in(30)],
        raw_events=enemy_kills,
    )
    rec = rule_elder_window(snap)
    assert rec is not None
    assert rec.kind == "elder_take"
    assert rec.severity == "alert"
    assert "VERHINDERN" in rec.text


def test_elder_window_free_take_when_enemies_dead() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    dead_enemy = _Player(champion_name="Draven", summoner_name="Draven", is_alive=False)
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[dead_enemy],
        objectives=[_elder_drake_in(20)],
        raw_events=_four_dragon_kills("Jinx"),
    )
    rec = rule_elder_window(snap)
    assert rec is not None
    assert rec.kind == "elder_take"
    assert rec.severity == "alert"
    assert "JETZT" in rec.text


def test_elder_window_no_soul_neither_team() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        objectives=[_elder_drake_in(50)],
    )
    rec = rule_elder_window(snap)
    assert rec is not None
    assert rec.kind == "elder_take"


def test_elder_window_in_all_rules() -> None:
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        objectives=[_elder_drake_in(30)],
        raw_events=_four_dragon_kills("Jinx"),
    )
    recs = evaluate(snap)
    assert any(r.kind == "elder_take" for r in recs)


def test_dragon_window_skips_elder() -> None:
    """rule_dragon_window must defer to rule_elder_window for Elder."""
    ally = _Player(champion_name="Jinx", summoner_name="Jinx")
    enemy = _Player(champion_name="Draven", summoner_name="Draven")
    snap = _Snap(
        game_time=600.0,
        allies=[ally],
        enemies=[enemy],
        objectives=[_elder_drake_in(20)],
    )
    assert rule_dragon_window(snap) is None


def test_elder_take_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("elder_take", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "elder_take" for r in result)


def test_elder_take_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("elder_take", "alert"),
        _rec("baron_take", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "elder_take" for r in result)
    assert not any(r.kind == "baron_take" for r in result)


# ----------------------------------------------------------------------
# rule_ally_turret_lost (B1 — defensive turret-loss signal)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    ALLY_TURRET_ALERT_WINDOW_S,
    rule_ally_turret_lost,
)


def _ally_turret_event(lane: str, tier: str, event_time: float, active_team: str = "ORDER") -> dict:
    """Build a TurretKilled event for an ally turret.
    active_team ORDER → ally side = TOrder; CHAOS → ally side = TChaos."""
    ally_side = "TOrder" if active_team == "ORDER" else "TChaos"
    turret_name = f"Turret_{ally_side}_{lane}_{tier}_01"
    return {
        "EventName": "TurretKilled",
        "TurretKilled": turret_name,
        "KillerName": "Enemy",
        "EventTime": event_time,
    }


def test_ally_turret_lost_silent_when_no_events() -> None:
    snap = _Snap(game_time=600.0, active_team="ORDER")
    assert rule_ally_turret_lost(snap) is None


def test_ally_turret_lost_silent_when_no_active_team() -> None:
    snap = _Snap(
        game_time=600.0,
        active_team="",
        raw_events=[_ally_turret_event("L1", "P1", 580.0, "ORDER")],
    )
    assert rule_ally_turret_lost(snap) is None


def test_ally_turret_lost_silent_when_too_old() -> None:
    snap = _Snap(
        game_time=700.0,
        active_team="ORDER",
        raw_events=[_ally_turret_event("L1", "P1", 500.0, "ORDER")],  # 200s ago
    )
    assert rule_ally_turret_lost(snap) is None


def test_ally_turret_lost_p1_fires_info() -> None:
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[_ally_turret_event("L1", "P1", 570.0, "ORDER")],
    )
    rec = rule_ally_turret_lost(snap)
    assert rec is not None
    assert rec.kind == "ally_turret_lost"
    assert rec.severity == "info"
    assert "Mid" in rec.text


def test_ally_turret_lost_p2_fires_warn() -> None:
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[_ally_turret_event("L0", "P2", 580.0, "ORDER")],
    )
    rec = rule_ally_turret_lost(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert "Bot" in rec.text


def test_ally_turret_lost_p3_fires_alert() -> None:
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[_ally_turret_event("L2", "P3", 590.0, "ORDER")],
    )
    rec = rule_ally_turret_lost(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert "Top" in rec.text
    assert "Inhib" in rec.text


def test_ally_turret_lost_escalates_to_highest_tier() -> None:
    """Two simultaneous turret losses — P2 wins over P1."""
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[
            _ally_turret_event("L1", "P1", 590.0, "ORDER"),
            _ally_turret_event("L0", "P2", 595.0, "ORDER"),
        ],
    )
    rec = rule_ally_turret_lost(snap)
    assert rec is not None
    assert rec.severity == "warn"  # P2


def test_ally_turret_lost_ignores_enemy_turrets() -> None:
    """TurretKilled for enemy side must NOT trigger ally_turret_lost."""
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[{
            "EventName": "TurretKilled",
            "TurretKilled": "Turret_TChaos_L1_P1_01",  # enemy turret
            "KillerName": "Ally",
            "EventTime": 595.0,
        }],
    )
    assert rule_ally_turret_lost(snap) is None


def test_ally_turret_lost_ttl_decreases_with_age() -> None:
    snap = _Snap(
        game_time=630.0,  # 30s after kill
        active_team="ORDER",
        raw_events=[_ally_turret_event("L1", "P1", 600.0, "ORDER")],
    )
    rec = rule_ally_turret_lost(snap)
    assert rec is not None
    assert rec.ttl_s == pytest.approx(30.0)


def test_ally_turret_lost_in_all_rules() -> None:
    snap = _Snap(
        game_time=600.0,
        active_team="ORDER",
        raw_events=[_ally_turret_event("L1", "P2", 580.0, "ORDER")],
    )
    recs = evaluate(snap)
    assert any(r.kind == "ally_turret_lost" for r in recs)


def test_ally_turret_lost_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("ally_turret_lost", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "ally_turret_lost" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


# ----------------------------------------------------------------------
# rule_dragon_soul_pressure (B1 — Dragon Soul momentum signal)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    DRAGON_SOUL_SIGNAL_S,
    rule_dragon_soul_pressure,
)


def _soul_dragon_kill(killer: str, event_time: float) -> dict:
    return {"EventName": "DragonKill", "KillerName": killer, "EventTime": event_time}


def _ally_with_name(name: str = "P1") -> _Player:
    return _Player(summoner_name=name, champion_name="Jinx")


def test_dragon_soul_pressure_silent_when_fewer_than_4_drakes() -> None:
    ally = _ally_with_name("P1")
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0]]
    snap = _Snap(game_time=320.0, allies=[ally], raw_events=events, active_team="ORDER")
    assert rule_dragon_soul_pressure(snap) is None


def test_dragon_soul_pressure_silent_when_window_expired() -> None:
    ally = _ally_with_name("P1")
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0, 400.0]]
    snap = _Snap(
        game_time=400.0 + DRAGON_SOUL_SIGNAL_S + 1.0,
        allies=[ally], raw_events=events, active_team="ORDER",
    )
    assert rule_dragon_soul_pressure(snap) is None


def test_dragon_soul_pressure_silent_with_no_allies() -> None:
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0, 400.0]]
    snap = _Snap(game_time=410.0, allies=[], raw_events=events, active_team="ORDER")
    assert rule_dragon_soul_pressure(snap) is None


def test_dragon_soul_pressure_fires_within_window() -> None:
    ally = _ally_with_name("P1")
    soul_time = 400.0
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0, soul_time]]
    snap = _Snap(
        game_time=soul_time + 30.0,
        allies=[ally], raw_events=events, active_team="ORDER",
    )
    rec = rule_dragon_soul_pressure(snap)
    assert rec is not None
    assert rec.kind == "dragon_soul"
    assert rec.severity == "info"
    assert rec.ttl_s == pytest.approx(DRAGON_SOUL_SIGNAL_S - 30.0)


def test_dragon_soul_pressure_ttl_decreases_with_age() -> None:
    ally = _ally_with_name("P1")
    soul_time = 500.0
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0, soul_time]]
    snap = _Snap(
        game_time=soul_time + DRAGON_SOUL_SIGNAL_S - 10.0,
        allies=[ally], raw_events=events, active_team="ORDER",
    )
    rec = rule_dragon_soul_pressure(snap)
    assert rec is not None
    assert rec.ttl_s == pytest.approx(10.0)


def test_dragon_soul_pressure_in_evaluate() -> None:
    ally = _ally_with_name("P1")
    soul_time = 600.0
    events = [_soul_dragon_kill("P1", t) for t in [100.0, 200.0, 300.0, soul_time]]
    snap = _Snap(
        game_time=soul_time + 10.0,
        allies=[ally], raw_events=events, active_team="ORDER",
    )
    recs = evaluate(snap)
    assert any(r.kind == "dragon_soul" for r in recs)


def test_dragon_soul_pressure_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("dragon_soul", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "dragon_soul" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


def test_dragon_soul_pressure_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("dragon_soul", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "dragon_soul" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


# ----------------------------------------------------------------------
# rule_void_grubs (B1 — early-game Void Grub objective)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    VOID_GRUB_HORNGUARD,
    VOID_GRUB_WINDOW_END_S,
    VOID_GRUB_WINDOW_START_S,
    rule_void_grubs,
)


def _grub_event(killer: str, event_time: float) -> dict:
    return {"EventName": "VoidGrub", "KillerName": killer, "EventTime": event_time}


def test_void_grubs_silent_before_window() -> None:
    snap = _Snap(game_time=VOID_GRUB_WINDOW_START_S - 1.0, active_team="ORDER")
    assert rule_void_grubs(snap) is None


def test_void_grubs_silent_after_window() -> None:
    snap = _Snap(game_time=VOID_GRUB_WINDOW_END_S + 1.0, active_team="ORDER")
    assert rule_void_grubs(snap) is None


def test_void_grubs_contest_shows_correct_needed_count() -> None:
    """Contest signal: 1 ally grub → needs 2 more for hornguard."""
    ally = _ally_with_name("P1")
    events = [_grub_event("P1", 300.0)]  # 1 ally grub
    snap = _Snap(game_time=320.0, allies=[ally], raw_events=events, active_team="ORDER")
    rec = rule_void_grubs(snap)
    assert rec is not None
    assert rec.kind == "void_grub_contest"
    assert "2" in rec.text  # needed = 3 - 1 = 2


def test_void_grubs_enemy_hornguard_fires_warn() -> None:
    ally = _ally_with_name("P1")
    enemy = _Player(summoner_name="Enemy", champion_name="Darius")
    events = [_grub_event("Enemy", 300.0 + i * 10) for i in range(VOID_GRUB_HORNGUARD)]
    snap = _Snap(game_time=340.0, allies=[ally], enemies=[enemy], raw_events=events, active_team="ORDER")
    rec = rule_void_grubs(snap)
    assert rec is not None
    assert rec.kind == "enemy_hornguard"
    assert rec.severity == "warn"
    assert str(VOID_GRUB_HORNGUARD) in rec.text


def test_void_grubs_ally_hornguard_fires_info() -> None:
    ally = _ally_with_name("P1")
    events = [_grub_event("P1", 300.0 + i * 10) for i in range(VOID_GRUB_HORNGUARD)]
    snap = _Snap(game_time=340.0, allies=[ally], raw_events=events, active_team="ORDER")
    rec = rule_void_grubs(snap)
    assert rec is not None
    assert rec.kind == "ally_hornguard"
    assert rec.severity == "info"


def test_void_grubs_contest_fires_when_neither_has_hornguard() -> None:
    ally = _ally_with_name("P1")
    events = [_grub_event("P1", 300.0), _grub_event("Enemy", 310.0)]  # 1 each
    snap = _Snap(game_time=330.0, allies=[ally], raw_events=events, active_team="ORDER")
    rec = rule_void_grubs(snap)
    assert rec is not None
    assert rec.kind == "void_grub_contest"
    assert rec.severity == "info"


def test_void_grubs_enemy_hornguard_takes_priority_over_ally() -> None:
    """Even if ally has 2 grubs, enemy at 3 triggers the defensive warn."""
    ally = _ally_with_name("P1")
    enemy = _Player(summoner_name="Enemy", champion_name="Darius")
    events = (
        [_grub_event("Enemy", 300.0 + i * 10) for i in range(VOID_GRUB_HORNGUARD)]
        + [_grub_event("P1", 360.0), _grub_event("P1", 370.0)]
    )
    snap = _Snap(game_time=380.0, allies=[ally], enemies=[enemy], raw_events=events, active_team="ORDER")
    rec = rule_void_grubs(snap)
    assert rec is not None
    assert rec.kind == "enemy_hornguard"


def test_void_grubs_in_evaluate() -> None:
    ally = _ally_with_name("P1")
    enemy = _Player(summoner_name="Enemy", champion_name="Darius")
    events = [_grub_event("Enemy", 280.0 + i * 10) for i in range(VOID_GRUB_HORNGUARD)]
    snap = _Snap(game_time=330.0, allies=[ally], enemies=[enemy], raw_events=events, active_team="ORDER")
    recs = evaluate(snap)
    assert any(r.kind == "enemy_hornguard" for r in recs)


def test_void_grubs_ally_hornguard_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("ally_hornguard", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "ally_hornguard" for r in result)


def test_void_grubs_enemy_hornguard_survives_numbers_disadv() -> None:
    """enemy_hornguard is defensive — must survive numbers_disadv."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("enemy_hornguard", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert any(r.kind == "enemy_hornguard" for r in result)


def test_void_grubs_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("void_grub_contest", "info"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "void_grub_contest" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


# ----------------------------------------------------------------------
# rule_enemy_jungler_down (B2 — enemy Smite-carrier dead → push/contest)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    JUNGLER_DOWN_MIN_S,
    JUNGLER_DOWN_OBJ_WINDOW_S,
    rule_enemy_jungler_down,
)
from champ_assistant.lcda.players import LiveSummonerSpell  # noqa: E402 (already imported above)


import types as _types  # noqa: E402

from champ_assistant.advisor.decision_engine import _is_jungler  # noqa: E402


@dataclass
class _JunglerEnemy:
    summoner_name: str = "JunglerEnemy"
    champion_name: str = "Vi"
    spell_one: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Smite", cooldown=90.0)
    )
    spell_two: LiveSummonerSpell = field(
        default_factory=lambda: LiveSummonerSpell(name="Flash", cooldown=300.0)
    )
    is_alive: bool = False
    respawn_timer: float = 20.0
    position: str = ""  # optional: set to "JUNGLE" to test position-based detection


def test_is_jungler_detects_smite() -> None:
    p = _JunglerEnemy(position="")
    assert _is_jungler(p)


def test_is_jungler_detects_position() -> None:
    """Position=JUNGLE should detect without Smite (e.g. not yet tracked)."""
    p = _Player(summoner_name="J", champion_name="Hecarim")  # no spells → no smite
    # Add position attribute manually (base _Player has no position field)
    p2 = _types.SimpleNamespace(position="JUNGLE", spell_one=None, spell_two=None)
    assert _is_jungler(p2)


def test_is_jungler_position_beats_smite_check() -> None:
    """When position=JUNGLE and no spells, still identified as jungler."""
    p = _types.SimpleNamespace(position="JUNGLE", spell_one=None, spell_two=None)
    assert _is_jungler(p)


def test_is_jungler_false_for_non_jungler() -> None:
    p = _Player(summoner_name="ADC", champion_name="Jinx")
    assert not _is_jungler(p)


def test_jungler_down_fires_with_position_based_detection() -> None:
    """Jungler detected via position=JUNGLE (no Smite in spells)."""
    jungler = _types.SimpleNamespace(
        position="JUNGLE",
        champion_name="Hecarim",
        summoner_name="J1",
        spell_one=LiveSummonerSpell(name="Flash", cooldown=300.0),
        spell_two=LiveSummonerSpell(name="Ghost", cooldown=210.0),  # no Smite
        respawn_timer=20.0,
        is_alive=False,
    )
    snap = _Snap(game_time=900.0, enemies=[jungler])
    rec = rule_enemy_jungler_down(snap)
    assert rec is not None
    assert rec.kind == "jungler_down"


def test_jungler_down_silent_when_no_enemies() -> None:
    snap = _Snap(game_time=600.0, enemies=[])
    assert rule_enemy_jungler_down(snap) is None


def test_jungler_down_silent_when_no_smite_carrier() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    snap = _Snap(game_time=600.0, enemies=[enemy])
    assert rule_enemy_jungler_down(snap) is None


def test_jungler_down_silent_when_respawn_too_short() -> None:
    jungler = _JunglerEnemy(respawn_timer=JUNGLER_DOWN_MIN_S - 1.0)
    snap = _Snap(game_time=600.0, enemies=[jungler])
    assert rule_enemy_jungler_down(snap) is None


def test_jungler_down_silent_when_alive() -> None:
    jungler = _JunglerEnemy(respawn_timer=0.0)
    snap = _Snap(game_time=600.0, enemies=[jungler])
    assert rule_enemy_jungler_down(snap) is None


def test_jungler_down_fires_warn_no_objective() -> None:
    jungler = _JunglerEnemy(respawn_timer=25.0)
    snap = _Snap(game_time=600.0, enemies=[jungler])
    rec = rule_enemy_jungler_down(snap)
    assert rec is not None
    assert rec.kind == "jungler_down"
    assert rec.severity == "warn"
    assert "Vi" in rec.text
    assert "25" in rec.text
    assert rec.ttl_s == pytest.approx(25.0)


def test_jungler_down_fires_alert_when_objective_soon() -> None:
    jungler = _JunglerEnemy(respawn_timer=30.0)
    dragon = _Objective(name="Dragon", next_spawn=600.0 + 40.0)  # 40s away, inside 60s window
    snap = _Snap(game_time=600.0, enemies=[jungler], objectives=[dragon])
    rec = rule_enemy_jungler_down(snap)
    assert rec is not None
    assert rec.kind == "jungler_down"
    assert rec.severity == "alert"
    assert "Objective" in rec.text or "sichern" in rec.text


def test_jungler_down_no_alert_when_objective_too_far() -> None:
    jungler = _JunglerEnemy(respawn_timer=30.0)
    dragon = _Objective(name="Dragon", next_spawn=600.0 + JUNGLER_DOWN_OBJ_WINDOW_S + 10.0)
    snap = _Snap(game_time=600.0, enemies=[jungler], objectives=[dragon])
    rec = rule_enemy_jungler_down(snap)
    assert rec is not None
    assert rec.severity == "warn"  # not alert


def test_jungler_down_ttl_matches_respawn() -> None:
    jungler = _JunglerEnemy(respawn_timer=42.0)
    snap = _Snap(game_time=600.0, enemies=[jungler])
    rec = rule_enemy_jungler_down(snap)
    assert rec is not None
    assert rec.ttl_s == pytest.approx(42.0)


def test_jungler_down_in_evaluate() -> None:
    jungler = _JunglerEnemy(respawn_timer=20.0)
    snap = _Snap(game_time=900.0, enemies=[jungler])  # past void-grub window
    recs = evaluate(snap)
    assert any(r.kind == "jungler_down" for r in recs)


def test_jungler_down_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("jungler_down", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "jungler_down" for r in result)
    assert any(r.kind == "numbers_disadv" for r in result)


def test_jungler_down_suppressed_by_ace() -> None:
    recs = [
        _rec("ace", "alert"),
        _rec("jungler_down", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "jungler_down" for r in result)


def test_jungler_down_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("jungler_down", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "jungler_down" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


# ----------------------------------------------------------------------
# rule_enemy_elder_buff (B4 — enemy Elder Drake buff → do not fight)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    ELDER_BUFF_DURATION_S,
    _enemy_elder_buff_remaining,
    rule_enemy_elder_buff,
)


def _enemy_elder_kill(killer: str, event_time: float) -> dict:
    return {
        "EventName": "DragonKill",
        "KillerName": killer,
        "EventTime": event_time,
        "DragonType": "Elder",
    }


def test_enemy_elder_buff_remaining_none_when_no_event() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    snap = _Snap(game_time=600.0, enemies=[enemy], raw_events=[])
    assert _enemy_elder_buff_remaining(snap) is None


def test_enemy_elder_buff_remaining_returns_correct_seconds() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    kill_time = 500.0
    snap = _Snap(
        game_time=550.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", kill_time)],
    )
    expected = ELDER_BUFF_DURATION_S - 50.0
    assert _enemy_elder_buff_remaining(snap) == pytest.approx(expected)


def test_enemy_elder_buff_remaining_none_when_expired() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    snap = _Snap(
        game_time=500.0 + ELDER_BUFF_DURATION_S + 1.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", 500.0)],
    )
    assert _enemy_elder_buff_remaining(snap) is None


def test_enemy_elder_buff_remaining_ignores_ally_elder() -> None:
    """Ally Elder kill must NOT show as enemy elder buff."""
    ally = _Player(champion_name="Jinx", summoner_name="P1")
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    snap = _Snap(
        game_time=550.0,
        allies=[ally], enemies=[enemy],
        raw_events=[_enemy_elder_kill("P1", 500.0)],  # ally killed it
    )
    assert _enemy_elder_buff_remaining(snap) is None


def test_rule_enemy_elder_buff_fires_alert_with_plenty_of_time() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 100.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", kill_time)],
    )
    rec = rule_enemy_elder_buff(snap)
    assert rec is not None
    assert rec.kind == "enemy_elder_buff"
    assert rec.severity == "alert"
    assert rec.category == "safety"
    assert "NICHT" in rec.text or "KÄMPFEN" in rec.text


def test_rule_enemy_elder_buff_fires_alert_when_expiring() -> None:
    """≤30s remaining → counter-engage window text."""
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 20.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", kill_time)],
    )
    rec = rule_enemy_elder_buff(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.category == "tempo"
    assert "Konter" in rec.text or "endet" in rec.text.lower()


def test_rule_enemy_elder_buff_silent_when_expired() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    snap = _Snap(
        game_time=600.0 + ELDER_BUFF_DURATION_S + 5.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", 600.0)],
    )
    assert rule_enemy_elder_buff(snap) is None


def test_enemy_elder_buff_survives_numbers_disadv() -> None:
    """Defensive alert (enemy has elder) must survive numbers_disadv suppression."""
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("enemy_elder_buff", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert any(r.kind == "enemy_elder_buff" for r in result)


def test_enemy_elder_buff_suppresses_fight_and_objectives() -> None:
    """When enemy has Elder, fight/baron/dragon take calls must be suppressed."""
    recs = [
        _rec("enemy_elder_buff", "alert"),
        _rec("fight", "warn"),
        _rec("baron_take", "alert"),
        _rec("dragon_take", "warn"),
        _rec("numbers_adv", "info"),
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "enemy_elder_buff" in kinds
    assert "fight" not in kinds
    assert "baron_take" not in kinds
    assert "dragon_take" not in kinds
    assert "numbers_adv" not in kinds


def test_enemy_elder_buff_in_evaluate() -> None:
    enemy = _Player(champion_name="Darius", summoner_name="E1")
    kill_time = 600.0 - (ELDER_BUFF_DURATION_S - 90.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_elder_kill("E1", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "enemy_elder_buff" for r in recs)


# ----------------------------------------------------------------------
# Cross-objective priority suppression (B3 — Rule 9: baron > dragon)
# ----------------------------------------------------------------------
def test_cross_obj_baron_beats_dragon_take() -> None:
    """When both baron_take and dragon_take fire, dragon_take is suppressed."""
    recs = [
        _rec("baron_take", "alert"),
        _rec("dragon_take", "warn"),
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "baron_take" in kinds
    assert "dragon_take" not in kinds


def test_cross_obj_baron_free_beats_dragon_take() -> None:
    recs = [
        _rec("baron_free", "alert"),
        _rec("dragon_take", "warn"),
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "baron_free" in kinds
    assert "dragon_take" not in kinds


def test_cross_obj_baron_beats_dragon_free() -> None:
    recs = [
        _rec("baron_take", "alert"),
        _rec("dragon_free", "warn"),
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "baron_take" in kinds
    assert "dragon_free" not in kinds


def test_cross_obj_dragon_alone_not_suppressed() -> None:
    """When only dragon fires (no baron), dragon card must survive."""
    recs = [_rec("dragon_take", "alert")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "dragon_take" for r in result)


def test_cross_obj_baron_alone_not_suppressed() -> None:
    recs = [_rec("baron_take", "alert")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "baron_take" for r in result)


# ----------------------------------------------------------------------
# rule_enemy_dragon_soul (B3 — enemy at soul point, persistent reminder)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    ENEMY_SOUL_POINT_HANDOFF_S,
    _enemy_drake_stack_count,
    rule_enemy_dragon_soul,
)


def _enemy_drake_kill(killer: str, event_time: float) -> dict:
    return {"EventName": "DragonKill", "KillerName": killer, "EventTime": event_time}


def test_enemy_dragon_soul_silent_when_fewer_than_3_stacks() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    events = [_enemy_drake_kill("E1", t) for t in [100.0, 200.0]]
    snap = _Snap(game_time=900.0, enemies=[enemy], raw_events=events)
    assert rule_enemy_dragon_soul(snap) is None


def test_enemy_dragon_soul_silent_when_4_or_more_stacks() -> None:
    """At 4 stacks enemy already has soul — this rule is silent (other rules handle it)."""
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    events = [_enemy_drake_kill("E1", t) for t in [100.0, 200.0, 300.0, 400.0]]
    snap = _Snap(game_time=900.0, enemies=[enemy], raw_events=events)
    assert rule_enemy_dragon_soul(snap) is None


def test_enemy_dragon_soul_fires_when_exactly_3_stacks_far_from_spawn() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    events = [_enemy_drake_kill("E1", t) for t in [100.0, 200.0, 300.0]]
    # Dragon spawns in 300s (well past the 120s handoff window)
    dragon = _Objective(name="Dragon", next_spawn=600.0 + 300.0)
    snap = _Snap(game_time=600.0, enemies=[enemy], raw_events=events, objectives=[dragon])
    rec = rule_enemy_dragon_soul(snap)
    assert rec is not None
    assert rec.kind == "enemy_soul_point"
    assert rec.severity == "warn"
    assert "3" in rec.text or "Soul" in rec.text


def test_enemy_dragon_soul_silent_when_dragon_within_handoff_window() -> None:
    """When dragon spawns within ENEMY_SOUL_POINT_HANDOFF_S, hand off to dragon_window."""
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    events = [_enemy_drake_kill("E1", t) for t in [100.0, 200.0, 300.0]]
    dragon = _Objective(name="Dragon", next_spawn=600.0 + ENEMY_SOUL_POINT_HANDOFF_S - 10.0)
    snap = _Snap(game_time=600.0, enemies=[enemy], raw_events=events, objectives=[dragon])
    assert rule_enemy_dragon_soul(snap) is None


def test_enemy_dragon_soul_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("enemy_soul_point", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_soul_point" for r in result)


def test_enemy_dragon_soul_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("enemy_soul_point", "warn"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_soul_point" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


def test_enemy_dragon_soul_in_evaluate() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    events = [_enemy_drake_kill("E1", t) for t in [100.0, 200.0, 300.0]]
    dragon = _Objective(name="Dragon", next_spawn=900.0 + 300.0)
    snap = _Snap(game_time=900.0, enemies=[enemy], raw_events=events, objectives=[dragon])
    recs = evaluate(snap)
    assert any(r.kind == "enemy_soul_point" for r in recs)


# ----------------------------------------------------------------------
# rule_ally_inhib_respawning (B4 — inhib about to respawn, go objectives)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import (  # noqa: E402
    ALLY_INHIB_RESPAWN_ALERT_S,
    INHIB_RESPAWN_S,
    _earliest_ally_inhib_respawn_remaining,
    rule_ally_inhib_respawning,
)


def _enemy_inhib_kill(killer: str, event_time: float) -> dict:
    return {"EventName": "InhibitorKilled", "KillerName": killer, "EventTime": event_time}


def _inhib_respawn_event() -> dict:
    return {"EventName": "InhibitorRespawned"}


def test_ally_inhib_respawn_remaining_none_when_no_events() -> None:
    snap = _Snap(game_time=600.0, enemies=[_Player(summoner_name="E1")])
    assert _earliest_ally_inhib_respawn_remaining(snap) is None


def test_ally_inhib_respawn_remaining_returns_correct_seconds() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    kill_time = 500.0
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_inhib_kill("E1", kill_time)],
    )
    expected = INHIB_RESPAWN_S - (600.0 - kill_time)
    assert _earliest_ally_inhib_respawn_remaining(snap) == pytest.approx(expected)


def test_ally_inhib_respawn_remaining_none_when_respawned() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    kill_time = 100.0
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_inhib_kill("E1", kill_time), _inhib_respawn_event()],
    )
    assert _earliest_ally_inhib_respawn_remaining(snap) is None


def test_ally_inhib_respawn_remaining_ignores_ally_kills() -> None:
    """Ally killed enemy inhib — must NOT count as ally inhib respawn."""
    ally = _Player(summoner_name="P1", champion_name="Jinx")
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    snap = _Snap(
        game_time=600.0,
        allies=[ally], enemies=[enemy],
        raw_events=[{"EventName": "InhibitorKilled", "KillerName": "P1", "EventTime": 500.0}],
    )
    assert _earliest_ally_inhib_respawn_remaining(snap) is None


def test_ally_inhib_respawning_silent_when_too_far() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    kill_time = 200.0  # 400s ago at game_time=600 → 300-400 = -100 → respawned long ago... wait
    # Need kill that respawns far from now: kill at 400 → respawns at 700, at game_time=600 → 100s left
    kill_time2 = 600.0 - (INHIB_RESPAWN_S - ALLY_INHIB_RESPAWN_ALERT_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_inhib_kill("E1", kill_time2)],
    )
    assert rule_ally_inhib_respawning(snap) is None


def test_ally_inhib_respawning_fires_when_within_alert_window() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    # Kill such that remaining = 30s (well within 60s alert window)
    kill_time = 600.0 - (INHIB_RESPAWN_S - 30.0)
    snap = _Snap(
        game_time=600.0,
        enemies=[enemy],
        raw_events=[_enemy_inhib_kill("E1", kill_time)],
    )
    rec = rule_ally_inhib_respawning(snap)
    assert rec is not None
    assert rec.kind == "ally_inhib_respawning"
    assert rec.severity == "info"
    assert "30" in rec.text
    assert rec.ttl_s == pytest.approx(30.0)


def test_ally_inhib_respawning_coexists_with_ally_inhib_down() -> None:
    """Both cards fire simultaneously when inhib is down but about to respawn.
    They are complementary: 'defend now' + 'back in 30s' tells the full story."""
    recs = [
        _rec("ally_inhib_down", "warn"),
        _rec("ally_inhib_respawning", "info"),
    ]
    result = _suppress_dominated(recs)
    assert any(r.kind == "ally_inhib_respawning" for r in result)
    assert any(r.kind == "ally_inhib_down" for r in result)


def test_ally_inhib_respawning_in_evaluate() -> None:
    enemy = _Player(summoner_name="E1", champion_name="Darius")
    kill_time = 900.0 - (INHIB_RESPAWN_S - 30.0)
    snap = _Snap(
        game_time=900.0,
        enemies=[enemy],
        raw_events=[_enemy_inhib_kill("E1", kill_time)],
    )
    recs = evaluate(snap)
    assert any(r.kind == "ally_inhib_respawning" for r in recs)


# ----------------------------------------------------------------------
# Power spike rule (B1 extension — fills gap: PowerSpikePanel is hidden
# during gameplay, so spikes must reach the RecommendationPanel)
# ----------------------------------------------------------------------
from champ_assistant.advisor.decision_engine import rule_power_spike  # noqa: E402
from champ_assistant.lcda.power_spikes import PowerSpike  # noqa: E402


def _spike(kind: str, value: int) -> PowerSpike:
    from champ_assistant.lcda.power_spikes import _label_for_level, _label_for_items
    if kind == "level":
        label, detail = _label_for_level(value)
    else:
        label, detail = _label_for_items(value)
    return PowerSpike(kind=kind, value=value, label=label, detail=detail)


def test_power_spike_silent_when_no_spikes() -> None:
    snap = _Snap(new_spikes=[])
    assert rule_power_spike(snap) is None


def test_power_spike_level_6_is_alert() -> None:
    snap = _Snap(new_spikes=[_spike("level", 6)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "power_spike"
    assert rec.category == "tempo"
    assert rec.confidence == 1.0
    assert rec.ttl_s == 20.0


def test_power_spike_level_11_is_warn() -> None:
    snap = _Snap(new_spikes=[_spike("level", 11)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert rec.ttl_s == 25.0


def test_power_spike_level_16_is_warn() -> None:
    snap = _Snap(new_spikes=[_spike("level", 16)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "warn"


def test_power_spike_two_items_is_warn() -> None:
    snap = _Snap(new_spikes=[_spike("items", 2)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert rec.ttl_s == 30.0


def test_power_spike_first_item_is_info() -> None:
    snap = _Snap(new_spikes=[_spike("items", 1)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "info"


def test_power_spike_text_includes_label_and_detail() -> None:
    snap = _Snap(new_spikes=[_spike("level", 6)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert "Ultimate is up" in rec.text
    assert "all-in" in rec.text


def test_power_spike_uses_last_spike_when_multiple() -> None:
    snap = _Snap(new_spikes=[_spike("level", 6), _spike("items", 1)])
    rec = rule_power_spike(snap)
    assert rec is not None
    assert rec.severity == "info"  # items=1 → info, not level-6 alert


def test_power_spike_suppressed_by_numbers_disadv() -> None:
    recs = [
        _rec("numbers_disadv", "alert"),
        _rec("power_spike", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "power_spike" for r in result)


def test_power_spike_survives_numbers_adv() -> None:
    recs = [
        _rec("numbers_adv", "warn"),
        _rec("power_spike", "alert"),
    ]
    result = _suppress_dominated(recs)
    assert any(r.kind == "power_spike" for r in result)


def test_power_spike_in_evaluate() -> None:
    snap = _Snap(new_spikes=[_spike("level", 6)])
    recs = evaluate(snap)
    assert any(r.kind == "power_spike" for r in recs)
