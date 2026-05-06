"""Tests for rule_objective_bounty_active — catch-up bounty awareness (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    OBJECTIVE_BOUNTY_DIFF_THRESHOLD,
    OBJECTIVE_BOUNTY_PHASE_END_S,
    OBJECTIVE_BOUNTY_PHASE_START_S,
    OBJECTIVE_BOUNTY_REARM_THRESHOLD,
    Recommendation,
    _suppress_dominated,
    reset_objective_bounty_hysteresis,
    rule_objective_bounty_active,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_objective_bounty_hysteresis()
    yield
    reset_objective_bounty_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Aggregate:
    items_value: int = 0
    kills: int = 0
    deaths: int = 0
    dragons: int = 0
    barons: int = 0
    heralds: int = 0


@dataclass
class _Snap:
    game_time: float = 900.0   # 15:00 — solidly in mid-game
    raw_events: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    active_team: str = "ORDER"
    active_summoner: str = "Me"
    active_level: int = 10
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


def _snap_with_diff(ally_value: int, enemy_value: int, *,
                    game_time: float = 900.0) -> _Snap:
    return _Snap(
        game_time=game_time,
        ally_aggregate=_Aggregate(items_value=ally_value),
        enemy_aggregate=_Aggregate(items_value=enemy_value),
    )


# ---------------------------------------------------------------------------
# Phase / threshold guards
# ---------------------------------------------------------------------------

def test_silent_before_phase_window() -> None:
    """At 5:00 — too early; bounties haven't accumulated meaningfully."""
    snap = _snap_with_diff(10000, 5000,
                          game_time=OBJECTIVE_BOUNTY_PHASE_START_S - 60)
    assert rule_objective_bounty_active(snap) is None


def test_silent_after_phase_window() -> None:
    """At 36:00 — too late; bounty mechanic stops mattering vs scaling."""
    snap = _snap_with_diff(20000, 14000,
                          game_time=OBJECTIVE_BOUNTY_PHASE_END_S + 60)
    assert rule_objective_bounty_active(snap) is None


def test_silent_below_threshold() -> None:
    """3k differential — below the 4.5k threshold, no bounty signal yet."""
    snap = _snap_with_diff(8000, 5000)
    assert rule_objective_bounty_active(snap) is None


def test_silent_when_no_aggregates() -> None:
    """Without team aggregates we can't compute the diff."""
    snap = _Snap(ally_aggregate=None, enemy_aggregate=None)
    assert rule_objective_bounty_active(snap) is None


# ---------------------------------------------------------------------------
# Behind branch
# ---------------------------------------------------------------------------

def test_behind_threshold_fires_info() -> None:
    """5k behind in mid-game → comeback bounty active."""
    snap = _snap_with_diff(5000, 10000)
    rec = rule_objective_bounty_active(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "objective_bounty_behind"
    assert "Bounties" in rec.text
    assert "Force" in rec.text or "force" in rec.text


def test_behind_message_includes_gold_gap() -> None:
    snap = _snap_with_diff(5000, 11000)  # -6k diff
    rec = rule_objective_bounty_active(snap)
    assert rec is not None
    assert "6" in rec.text  # mentions the gap


# ---------------------------------------------------------------------------
# Ahead branch
# ---------------------------------------------------------------------------

def test_ahead_threshold_fires_info() -> None:
    """5k ahead → bounty-on-our-deaths warning."""
    snap = _snap_with_diff(11000, 6000)
    rec = rule_objective_bounty_active(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "objective_bounty_ahead"
    assert "Vorsicht" in rec.text or "vorsichtig" in rec.text.lower()


def test_ahead_message_mentions_shutdown_gold() -> None:
    snap = _snap_with_diff(11000, 6000)
    rec = rule_objective_bounty_active(snap)
    assert rec is not None
    assert "Shutdown" in rec.text or "Bounty" in rec.text


# ---------------------------------------------------------------------------
# Hysteresis — fire once per state crossing
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_direction() -> None:
    snap = _snap_with_diff(5000, 10000)
    first = rule_objective_bounty_active(snap)
    second = rule_objective_bounty_active(snap)
    assert first is not None
    assert second is None


def test_re_arms_when_gap_closes() -> None:
    """Behind 5k → fire. Gap closes to 1k → re-arm. Behind 5k again → fire."""
    rule_objective_bounty_active(_snap_with_diff(5000, 10000))
    # Gap closes (we won fights, items_value caught up).
    rule_objective_bounty_active(_snap_with_diff(9000, 10000))   # diff = -1k
    # Behind 5k again.
    rec = rule_objective_bounty_active(_snap_with_diff(5000, 10000))
    assert rec is not None


def test_does_not_arm_within_threshold_band() -> None:
    """When diff oscillates between rearm and threshold, don't fire repeatedly."""
    rule_objective_bounty_active(_snap_with_diff(5000, 10000))  # -5k → fire
    # Stay in [-3k, -5k] band — should not re-fire.
    rec1 = rule_objective_bounty_active(_snap_with_diff(6500, 10000))  # -3.5k
    rec2 = rule_objective_bounty_active(_snap_with_diff(5500, 10000))  # -4.5k
    assert rec1 is None
    assert rec2 is None


def test_behind_then_ahead_each_fire() -> None:
    """Cross from -5k behind to +5k ahead — both directions get one fire."""
    rec_behind = rule_objective_bounty_active(_snap_with_diff(5000, 10000))
    # Need to cross through the rearm threshold to clear `fired_behind`.
    rule_objective_bounty_active(_snap_with_diff(8000, 8500))  # near zero
    rec_ahead = rule_objective_bounty_active(_snap_with_diff(11000, 6000))
    assert rec_behind is not None and rec_behind.kind == "objective_bounty_behind"
    assert rec_ahead is not None and rec_ahead.kind == "objective_bounty_ahead"


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_behind_suppressed_by_far_behind_safe() -> None:
    """At deep deficits (> 5k), 'play safe' wins over 'force objectives'."""
    recs = [_rec("far_behind_safe", "warn"),
            _rec("objective_bounty_behind", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_bounty_behind" for r in out)


def test_ahead_survives_far_behind_safe() -> None:
    """far_behind_safe wouldn't fire when ahead — both can coexist if it does."""
    recs = [_rec("far_behind_safe", "warn"),
            _rec("objective_bounty_ahead", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "objective_bounty_ahead" for r in out)


def test_both_branches_suppressed_by_ace() -> None:
    recs = [
        _rec("ace", "alert"),
        _rec("objective_bounty_behind", "info"),
        _rec("objective_bounty_ahead", "info"),
    ]
    out = _suppress_dominated(recs)
    kinds = {r.kind for r in out}
    assert "objective_bounty_behind" not in kinds
    assert "objective_bounty_ahead" not in kinds


def test_both_branches_suppressed_by_ally_inhib_down() -> None:
    recs = [
        _rec("ally_inhib_down", "alert"),
        _rec("objective_bounty_behind", "info"),
        _rec("objective_bounty_ahead", "info"),
    ]
    out = _suppress_dominated(recs)
    kinds = {r.kind for r in out}
    assert "objective_bounty_behind" not in kinds
    assert "objective_bounty_ahead" not in kinds


def test_both_branches_suppressed_by_spiral_tilt() -> None:
    recs = [
        _rec("tilt", "alert"),
        _rec("objective_bounty_behind", "info"),
        _rec("objective_bounty_ahead", "info"),
    ]
    out = _suppress_dominated(recs)
    kinds = {r.kind for r in out}
    assert "objective_bounty_behind" not in kinds
    assert "objective_bounty_ahead" not in kinds


def test_survives_normal_tilt() -> None:
    recs = [_rec("tilt", "warn"), _rec("objective_bounty_behind", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "objective_bounty_behind" for r in out)
