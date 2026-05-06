"""Tests for rule_objective_setup_window — pre-spawn objective coaching (B3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from champ_assistant.advisor.decision_engine import (
    Recommendation,
    SETUP_WINDOW_MAX_S,
    SETUP_WINDOW_MIN_S,
    _suppress_dominated,
    rule_objective_setup_window,
)
from champ_assistant.lcda.objectives import ObjectiveTimer


# ---------------------------------------------------------------------------
# Stub helpers — minimal snapshot
# ---------------------------------------------------------------------------

@dataclass
class _Player:
    summoner_name: str = "Me"
    champion_name: str = "Yasuo"
    position: str = "MIDDLE"


@dataclass
class _Snap:
    game_time: float = 240.0
    objectives: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    raw_events: list = field(default_factory=list)
    active_team: str = ""
    active_summoner: str = "Me"
    active_level: int = 5
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


def _obj(name: str, remaining: float, *, game_time: float = 240.0) -> ObjectiveTimer:
    """Build an ObjectiveTimer that reports ``remaining`` at the given
    game_time. ObjectiveTimer stores absolute spawn time, so we add it to
    game_time to land the requested remaining seconds at call time."""
    return ObjectiveTimer(
        name=name,
        next_spawn_seconds=game_time + remaining,
        last_killed_seconds=None,
    )


def _snap_with(objective: ObjectiveTimer | None, *, position: str = "MIDDLE",
               game_time: float = 240.0) -> _Snap:
    snap = _Snap(
        game_time=game_time,
        objectives=[objective] if objective is not None else [],
        allies=[_Player(position=position)],
    )
    return snap


# ---------------------------------------------------------------------------
# Window guards
# ---------------------------------------------------------------------------

def test_no_rec_when_no_objectives() -> None:
    assert rule_objective_setup_window(_Snap()) is None


def test_no_rec_below_setup_min() -> None:
    """Less than 30 s out — handled by the up-window rules, not this one."""
    snap = _snap_with(_obj("Dragon", SETUP_WINDOW_MIN_S - 5))
    assert rule_objective_setup_window(snap) is None


def test_no_rec_above_setup_max() -> None:
    """More than 90 s out — too far to influence the wave that'll crash."""
    snap = _snap_with(_obj("Dragon", SETUP_WINDOW_MAX_S + 30))
    assert rule_objective_setup_window(snap) is None


def test_no_rec_for_unknown_objective_name() -> None:
    snap = _snap_with(_obj("MysteryGoat", 60.0))
    assert rule_objective_setup_window(snap) is None


def test_no_rec_when_remaining_is_none() -> None:
    """ObjectiveTimer with next_spawn_seconds=None never spawns again."""
    obj = ObjectiveTimer(name="Dragon", next_spawn_seconds=None, last_killed_seconds=None)
    snap = _snap_with(obj)
    assert rule_objective_setup_window(snap) is None


# ---------------------------------------------------------------------------
# Tier firing
# ---------------------------------------------------------------------------

def test_dragon_setup_fires() -> None:
    snap = _snap_with(_obj("Dragon", 60.0), position="BOTTOM")
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert rec.kind == "objective_setup"
    assert rec.severity == "info"
    assert "Drache" in rec.text
    assert "60" in rec.text


def test_baron_setup_fires() -> None:
    snap = _snap_with(_obj("Baron", 75.0, game_time=1500.0), position="MIDDLE",
                      game_time=1500.0)
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert "Baron" in rec.text


def test_herald_setup_fires() -> None:
    snap = _snap_with(_obj("Herald", 50.0, game_time=840.0), position="TOP",
                      game_time=840.0)
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert "Herald" in rec.text


def test_void_grubs_setup_fires() -> None:
    snap = _snap_with(_obj("VoidGrubs", 60.0, game_time=240.0), position="TOP",
                      game_time=240.0)
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert "Void Grubs" in rec.text


# ---------------------------------------------------------------------------
# Priority — multiple objectives in the window at once
# ---------------------------------------------------------------------------

def test_baron_beats_dragon_when_both_in_window() -> None:
    """Baron is most game-defining — pros contest it first."""
    objs = [
        _obj("Dragon", 60.0, game_time=1500.0),
        _obj("Baron", 70.0, game_time=1500.0),
    ]
    snap = _Snap(game_time=1500.0, objectives=objs, allies=[_Player(position="MIDDLE")])
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert "Baron" in rec.text


def test_dragon_beats_herald_when_both_in_window() -> None:
    objs = [
        _obj("Herald", 60.0, game_time=900.0),
        _obj("Dragon", 60.0, game_time=900.0),
    ]
    snap = _Snap(game_time=900.0, objectives=objs, allies=[_Player(position="MIDDLE")])
    rec = rule_objective_setup_window(snap)
    assert rec is not None
    assert "Drache" in rec.text


# ---------------------------------------------------------------------------
# Position-aware advice
# ---------------------------------------------------------------------------

def test_dragon_advice_differs_for_bot_vs_top() -> None:
    bot_rec = rule_objective_setup_window(
        _snap_with(_obj("Dragon", 60.0), position="BOTTOM")
    )
    top_rec = rule_objective_setup_window(
        _snap_with(_obj("Dragon", 60.0), position="TOP")
    )
    assert bot_rec is not None and top_rec is not None
    # BOT is near the drake pit; TOP needs to TP
    assert bot_rec.text != top_rec.text
    assert "TP" in top_rec.text  # top-side rotation advice mentions TP


def test_baron_advice_for_top_lane_near_pit() -> None:
    rec = rule_objective_setup_window(
        _snap_with(_obj("Baron", 60.0, game_time=1500.0),
                   position="TOP", game_time=1500.0)
    )
    assert rec is not None
    assert "Vision" in rec.text or "Pit" in rec.text or "Tank" in rec.text


def test_jungle_advice_mentions_smite() -> None:
    rec = rule_objective_setup_window(
        _snap_with(_obj("Dragon", 60.0), position="JUNGLE")
    )
    assert rec is not None
    assert "Smite" in rec.text


def test_utility_treated_as_near_drake_pit() -> None:
    """Bot 2-vs-2: support shares the pit-side advice with their ADC."""
    bot_rec = rule_objective_setup_window(
        _snap_with(_obj("Dragon", 60.0), position="BOTTOM")
    )
    util_rec = rule_objective_setup_window(
        _snap_with(_obj("Dragon", 60.0), position="UTILITY")
    )
    assert bot_rec is not None and util_rec is not None
    assert bot_rec.text == util_rec.text


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "info") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="objective",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_setup_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_setup" for r in out)


def test_setup_suppressed_by_numbers_disadv() -> None:
    """Don't tell short-handed players to rotate to objectives."""
    recs = [_rec("numbers_disadv", "warn"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_setup" for r in out)


def test_setup_suppressed_by_ally_inhib_down() -> None:
    """Defending base trumps objective prep."""
    recs = [_rec("ally_inhib_down", "alert"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_setup" for r in out)


def test_setup_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_setup" for r in out)


def test_setup_suppressed_by_enemy_elder() -> None:
    """Enemy has elder execute — don't prep any objective fights."""
    recs = [_rec("enemy_elder_buff", "alert"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_setup" for r in out)


def test_setup_survives_normal_tilt() -> None:
    """Non-spiral tilt (warn) shouldn't kill the setup window."""
    recs = [_rec("tilt", "warn"), _rec("objective_setup", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "objective_setup" for r in out)
