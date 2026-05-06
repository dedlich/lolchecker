"""Tests for rule_matchup_mismatch — per-enemy lane-deficit coaching (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    MISMATCH_DEFICIT_INFO,
    MISMATCH_DEFICIT_WARN,
    Recommendation,
    _matchup_deficit,
    _suppress_dominated,
    reset_matchup_mismatch_hysteresis,
    rule_matchup_mismatch,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_matchup_mismatch_hysteresis()
    yield
    reset_matchup_mismatch_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

ACTIVE = "Me"
# Use a distinctive active-player champion name. Real games can't have
# two players on the same champion, so this mirrors reality and avoids
# active_ids accidentally matching enemy KillerName/VictimName fields.
ACTIVE_CHAMP = "MyChamp"


@dataclass
class _Player:
    summoner_name: str = ACTIVE
    champion_name: str = ACTIVE_CHAMP
    team: str = "ORDER"
    position: str = "MIDDLE"


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


def _kill(killer: str, victim: str, t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


# ---------------------------------------------------------------------------
# _matchup_deficit pure-helper tests
# ---------------------------------------------------------------------------

def test_deficit_counts_deaths_to_each_enemy() -> None:
    events = [
        _kill(killer="Yasuo", victim=ACTIVE, t=100.0),
        _kill(killer="Yasuo", victim=ACTIVE, t=200.0),
        _kill(killer="Ahri", victim=ACTIVE, t=300.0),
    ]
    d = _matchup_deficit({ACTIVE, ACTIVE_CHAMP}, events)
    assert d["Yasuo"] == 2
    assert d["Ahri"] == 1


def test_deficit_subtracts_kills_on_each_enemy() -> None:
    events = [
        _kill(killer="Yasuo", victim=ACTIVE, t=100.0),
        _kill(killer=ACTIVE, victim="Yasuo", t=200.0),  # revenge
    ]
    d = _matchup_deficit({ACTIVE, ACTIVE_CHAMP}, events)
    assert d["Yasuo"] == 0


def test_deficit_negative_when_winning() -> None:
    events = [
        _kill(killer=ACTIVE, victim="Yasuo", t=100.0),
        _kill(killer=ACTIVE, victim="Yasuo", t=200.0),
    ]
    d = _matchup_deficit({ACTIVE, ACTIVE_CHAMP}, events)
    assert d["Yasuo"] == -2


def test_deficit_uses_both_summoner_and_champion_name() -> None:
    """LCDA may use either summoner_name OR champion_name as KillerName."""
    events = [
        _kill(killer="Yasuo", victim=ACTIVE, t=100.0),
        _kill(killer="Yasuo", victim=ACTIVE_CHAMP, t=200.0),  # via champ name
    ]
    d = _matchup_deficit({ACTIVE, ACTIVE_CHAMP}, events)
    assert d["Yasuo"] == 2


# ---------------------------------------------------------------------------
# Tier firing
# ---------------------------------------------------------------------------

def _snap_with_deaths_to(killer: str, count: int, *, kills_on: int = 0) -> _Snap:
    events: list = []
    for i in range(count):
        events.append(_kill(killer=killer, victim=ACTIVE, t=100.0 + i * 30.0))
    for i in range(kills_on):
        events.append(_kill(killer=ACTIVE, victim=killer, t=500.0 + i * 30.0))
    return _Snap(raw_events=events, allies=[_Player()])


def test_silent_below_deficit_threshold() -> None:
    """1 death (deficit 1) — not yet a matchup mismatch."""
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 1))
    assert rec is None


def test_info_tier_at_deficit_2() -> None:
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", MISMATCH_DEFICIT_INFO))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "matchup_mismatch"
    assert "Yasuo" in rec.text
    assert str(MISMATCH_DEFICIT_INFO) in rec.text


def test_warn_tier_at_deficit_3() -> None:
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", MISMATCH_DEFICIT_WARN))
    assert rec is not None
    assert rec.severity == "warn"
    assert "dominiert" in rec.text


def test_warn_tier_at_higher_deficits() -> None:
    """Deficit 5+ stays at warn — no further escalation tier."""
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 5))
    assert rec is not None
    assert rec.severity == "warn"


# ---------------------------------------------------------------------------
# Trade-deaths-with-kills math
# ---------------------------------------------------------------------------

def test_silent_when_deficit_offset_by_kills() -> None:
    """3 deaths to Yasuo BUT 2 kills back → net deficit 1, no fire."""
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 3, kills_on=2))
    assert rec is None


def test_silent_when_winning_matchup() -> None:
    """3 kills on Yasuo, 0 deaths → negative deficit, no fire."""
    snap = _snap_with_deaths_to("Yasuo", 0, kills_on=3)
    rec = rule_matchup_mismatch(snap)
    assert rec is None


def test_silent_when_kill_pattern_balanced() -> None:
    """Trading kills evenly — no mismatch flag."""
    rec = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 2, kills_on=2))
    assert rec is None


# ---------------------------------------------------------------------------
# Per-enemy hysteresis
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_tier() -> None:
    snap = _snap_with_deaths_to("Yasuo", 2)
    first = rule_matchup_mismatch(snap)
    second = rule_matchup_mismatch(snap)
    assert first is not None
    assert second is None


def test_escalates_when_deficit_grows() -> None:
    rec_2 = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 2))
    rec_3 = rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 3))
    assert rec_2 is not None and rec_2.severity == "info"
    assert rec_3 is not None and rec_3.severity == "warn"


def test_separate_enemies_track_separately() -> None:
    """Yasuo deficit 3 announced; Ahri deficit 2 in same game still fires."""
    rule_matchup_mismatch(_snap_with_deaths_to("Yasuo", 3))
    snap = _Snap(
        raw_events=[
            _kill(killer="Yasuo", victim=ACTIVE, t=100.0),
            _kill(killer="Yasuo", victim=ACTIVE, t=110.0),
            _kill(killer="Yasuo", victim=ACTIVE, t=120.0),
            _kill(killer="Ahri", victim=ACTIVE, t=200.0),
            _kill(killer="Ahri", victim=ACTIVE, t=210.0),
        ],
        allies=[_Player()],
    )
    rec = rule_matchup_mismatch(snap)
    assert rec is not None
    assert "Ahri" in rec.text


def test_picks_highest_deficit_enemy() -> None:
    """Multiple enemies with deficits — pick the worst one."""
    snap = _Snap(
        raw_events=[
            _kill(killer="Yasuo", victim=ACTIVE, t=100.0),
            _kill(killer="Yasuo", victim=ACTIVE, t=110.0),
            _kill(killer="Ahri", victim=ACTIVE, t=200.0),
            _kill(killer="Ahri", victim=ACTIVE, t=210.0),
            _kill(killer="Ahri", victim=ACTIVE, t=220.0),
            _kill(killer="Ahri", victim=ACTIVE, t=230.0),
        ],
        allies=[_Player()],
    )
    rec = rule_matchup_mismatch(snap)
    assert rec is not None
    assert "Ahri" in rec.text  # 4-deficit beats Yasuo's 2-deficit


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_silent_when_no_active_player() -> None:
    """No allies → can't identify active player → no recs."""
    snap = _Snap(allies=[], raw_events=[_kill(killer="Yasuo", victim=ACTIVE)])
    assert rule_matchup_mismatch(snap) is None


def test_silent_when_no_events() -> None:
    snap = _Snap(allies=[_Player()], raw_events=[])
    assert rule_matchup_mismatch(snap) is None


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="lane",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("matchup_mismatch", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "matchup_mismatch" for r in out)


def test_suppressed_by_numbers_disadv() -> None:
    recs = [_rec("numbers_disadv", "warn"), _rec("matchup_mismatch", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "matchup_mismatch" for r in out)


def test_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("matchup_mismatch", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "matchup_mismatch" for r in out)


def test_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("matchup_mismatch", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "matchup_mismatch" for r in out)


def test_survives_normal_tilt() -> None:
    """Non-spiral tilt shouldn't drop matchup info — they're complementary."""
    recs = [_rec("tilt", "warn"), _rec("matchup_mismatch", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "matchup_mismatch" for r in out)
