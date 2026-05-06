"""Tests for rule_teamfight_outcome — post-fight conversion / recovery (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    Recommendation,
    TEAMFIGHT_DECISIVE_NET,
    TEAMFIGHT_LOPSIDED_NET,
    TEAMFIGHT_MIN_TOTAL_KILLS,
    TEAMFIGHT_WINDOW_S,
    _suppress_dominated,
    reset_teamfight_outcome_hysteresis,
    rule_teamfight_outcome,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_teamfight_outcome_hysteresis()
    yield
    reset_teamfight_outcome_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

ACTIVE = "Me"
ACTIVE_CHAMP = "MyChamp"


@dataclass
class _Player:
    summoner_name: str = ""
    champion_name: str = ""


@dataclass
class _Snap:
    game_time: float = 600.0
    raw_events: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    active_team: str = "ORDER"
    active_summoner: str = ACTIVE
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


def _kill(killer: str, victim: str = "Other", t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


# Five-man teams.
ALLIES = [
    _Player(summoner_name=ACTIVE, champion_name=ACTIVE_CHAMP),
    _Player(summoner_name="A2", champion_name="A2"),
    _Player(summoner_name="A3", champion_name="A3"),
    _Player(summoner_name="A4", champion_name="A4"),
    _Player(summoner_name="A5", champion_name="A5"),
]
ENEMIES = [
    _Player(summoner_name=f"E{i}", champion_name=f"E{i}") for i in range(1, 6)
]


def _fight(ally_kills: int, enemy_kills: int, *, anchor_t: float = 600.0) -> _Snap:
    """Generate a snapshot whose raw_events contain ``ally_kills`` ally-team
    kills + ``enemy_kills`` enemy-team kills, all clustered around ``anchor_t``."""
    events: list = []
    for i in range(ally_kills):
        events.append(_kill(killer=f"A{(i % 4) + 2}", victim=f"E{i + 1}",
                            t=anchor_t - i * 1.5))
    for i in range(enemy_kills):
        events.append(_kill(killer=f"E{(i % 4) + 1}", victim=f"A{(i % 4) + 2}",
                            t=anchor_t - 1 - i * 1.5))
    return _Snap(
        game_time=anchor_t + 2.0,  # we evaluate just after the fight
        raw_events=events, allies=ALLIES, enemies=ENEMIES,
    )


# ---------------------------------------------------------------------------
# Window guards
# ---------------------------------------------------------------------------

def test_silent_with_too_few_kills() -> None:
    """2 kills isn't a teamfight (could be a 2v0 gank)."""
    rec = rule_teamfight_outcome(_fight(2, 0))
    assert rec is None


def test_silent_when_kills_outside_window() -> None:
    """3 kills total but spread over 30s — not the same fight."""
    snap = _Snap(
        game_time=600.0,
        raw_events=[
            _kill("A2", "E1", t=550.0),  # 50s before anchor
            _kill("A3", "E2", t=560.0),
            _kill("A4", "E3", t=600.0),  # only 1 in the 15s window
        ],
        allies=ALLIES, enemies=ENEMIES,
    )
    rec = rule_teamfight_outcome(snap)
    assert rec is None


def test_silent_for_trade_fight() -> None:
    """3-3 trade — net 0, no decisive coaching."""
    rec = rule_teamfight_outcome(_fight(3, 3))
    assert rec is None


def test_silent_for_marginal_lead() -> None:
    """2-1 fight — net 1 (below DECISIVE_NET=2), no fire."""
    rec = rule_teamfight_outcome(_fight(2, 1))
    assert rec is None


def test_silent_with_no_events() -> None:
    snap = _Snap(allies=ALLIES, enemies=ENEMIES)
    assert rule_teamfight_outcome(snap) is None


def test_silent_when_team_lists_empty() -> None:
    """Without ally/enemy lists we can't classify killers — bail."""
    snap = _fight(3, 0)
    snap.allies = []
    snap.enemies = []
    assert rule_teamfight_outcome(snap) is None


# ---------------------------------------------------------------------------
# Win branches
# ---------------------------------------------------------------------------

def test_won_3_0_fires_alert() -> None:
    """3-0 sweep — lopsided, should fire alert."""
    rec = rule_teamfight_outcome(_fight(3, 0))
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "teamfight_won_big"
    assert "GEWONNEN" in rec.text
    assert "3-0" in rec.text


def test_won_3_1_fires_info() -> None:
    """3-1 (net 2) — decisive but not lopsided, info severity."""
    rec = rule_teamfight_outcome(_fight(3, 1))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "teamfight_won"
    assert "gewonnen" in rec.text


def test_won_4_2_fires_info() -> None:
    rec = rule_teamfight_outcome(_fight(4, 2))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "teamfight_won"


# ---------------------------------------------------------------------------
# Loss branches
# ---------------------------------------------------------------------------

def test_lost_0_3_fires_alert() -> None:
    """0-3 wipe of allies — alert "kein engage" call."""
    rec = rule_teamfight_outcome(_fight(0, 3))
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "teamfight_lost_big"
    assert "VERLOREN" in rec.text


def test_lost_1_3_fires_warn() -> None:
    rec = rule_teamfight_outcome(_fight(1, 3))
    assert rec is not None
    assert rec.severity == "warn"
    assert rec.kind == "teamfight_lost"


# ---------------------------------------------------------------------------
# Phase-aware advice
# ---------------------------------------------------------------------------

def test_won_advice_differs_early_vs_late() -> None:
    early = rule_teamfight_outcome(_fight(3, 0, anchor_t=500.0))
    reset_teamfight_outcome_hysteresis()
    late = rule_teamfight_outcome(_fight(3, 0, anchor_t=1700.0))
    assert early is not None and late is not None
    assert early.text != late.text  # advice changes by phase


def test_lost_advice_mentions_defensive_actions() -> None:
    early = rule_teamfight_outcome(_fight(0, 3, anchor_t=500.0))
    assert early is not None
    assert ("Recall" in early.text or "freezen" in early.text or
            "defensiv" in early.text.lower())


def test_late_game_won_advice_mentions_baron() -> None:
    rec = rule_teamfight_outcome(_fight(3, 0, anchor_t=1700.0))
    assert rec is not None
    assert "Baron" in rec.text


# ---------------------------------------------------------------------------
# Hysteresis — fire once per fight
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_fight() -> None:
    snap = _fight(3, 0)
    first = rule_teamfight_outcome(snap)
    second = rule_teamfight_outcome(snap)
    assert first is not None
    assert second is None


def test_re_fires_on_separate_later_fight() -> None:
    """Two fights, well-separated in time — both should fire."""
    rule_teamfight_outcome(_fight(3, 0, anchor_t=500.0))
    rec2 = rule_teamfight_outcome(_fight(0, 3, anchor_t=900.0))  # 400s later
    assert rec2 is not None


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_won_suppressed_by_ace() -> None:
    """ace already includes the conversion call — won is redundant."""
    recs = [_rec("ace", "alert"), _rec("teamfight_won_big", "alert")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "teamfight_won_big" for r in out)


def test_won_suppressed_by_numbers_disadv() -> None:
    """Can't claim 'we won' while short-handed."""
    recs = [_rec("numbers_disadv", "warn"), _rec("teamfight_won", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "teamfight_won" for r in out)


def test_lost_survives_numbers_disadv() -> None:
    """teamfight_lost EXPLAINS numbers_disadv — keep both."""
    recs = [_rec("numbers_disadv", "warn"), _rec("teamfight_lost", "warn")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "teamfight_lost" for r in out)


def test_both_suppressed_by_ally_inhib_down() -> None:
    """Defending base trumps any post-fight commentary."""
    recs = [
        _rec("ally_inhib_down", "alert"),
        _rec("teamfight_won", "info"),
        _rec("teamfight_lost", "warn"),
    ]
    out = _suppress_dominated(recs)
    kinds = {r.kind for r in out}
    assert "teamfight_won" not in kinds
    assert "teamfight_lost" not in kinds


def test_won_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("teamfight_won", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "teamfight_won" for r in out)


def test_lost_survives_normal_tilt() -> None:
    """Tilt + lost fight — both useful, complementary."""
    recs = [_rec("tilt", "warn"), _rec("teamfight_lost", "warn")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "teamfight_lost" for r in out)
