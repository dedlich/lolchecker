"""Tests for rule_objective_taken_by_ally — at-moment-of-kill conversion (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    OBJECTIVE_TAKEN_RECENT_S,
    Recommendation,
    _suppress_dominated,
    reset_objective_taken_hysteresis,
    rule_objective_taken_by_ally,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_objective_taken_hysteresis()
    yield
    reset_objective_taken_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Player:
    summoner_name: str = ""
    champion_name: str = ""


@dataclass
class _AllyAggregate:
    dragons: int = 0
    barons: int = 0
    heralds: int = 0


@dataclass
class _Snap:
    game_time: float = 1500.0
    raw_events: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    active_team: str = "ORDER"
    active_summoner: str = "Me"
    active_level: int = 12
    active_items: int = 2
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


ALLIES = [
    _Player(summoner_name="Me", champion_name="MyChamp"),
    _Player(summoner_name="A2", champion_name="A2"),
    _Player(summoner_name="A3", champion_name="A3"),
    _Player(summoner_name="A4", champion_name="A4"),
    _Player(summoner_name="A5", champion_name="A5"),
]
ENEMIES = [
    _Player(summoner_name=f"E{i}", champion_name=f"E{i}") for i in range(1, 6)
]


def _objective_kill(name: str, killer: str, t: float, *,
                    dragon_type: str = "") -> dict:
    evt = {
        "EventName": name,
        "EventTime": t,
        "KillerName": killer,
        "Assisters": [],
    }
    if dragon_type:
        evt["DragonType"] = dragon_type
    return evt


# ---------------------------------------------------------------------------
# Window guards
# ---------------------------------------------------------------------------

def test_silent_when_no_events() -> None:
    snap = _Snap(allies=ALLIES, enemies=ENEMIES)
    assert rule_objective_taken_by_ally(snap) is None


def test_silent_when_only_enemy_kills() -> None:
    """An enemy took baron — different rule's job."""
    snap = _Snap(
        raw_events=[_objective_kill("BaronKill", "E1", t=1490.0)],
        allies=ALLIES, enemies=ENEMIES,
    )
    assert rule_objective_taken_by_ally(snap) is None


def test_silent_when_kill_too_old() -> None:
    """Baron kill from 30s ago — past the OBJECTIVE_TAKEN_RECENT_S window."""
    snap = _Snap(
        game_time=1500.0,
        raw_events=[_objective_kill("BaronKill", "A2",
                                    t=1500.0 - OBJECTIVE_TAKEN_RECENT_S - 5)],
        allies=ALLIES, enemies=ENEMIES,
    )
    assert rule_objective_taken_by_ally(snap) is None


def test_silent_when_no_allies() -> None:
    """Without ally identifiers we can't classify the killer's team."""
    snap = _Snap(
        raw_events=[_objective_kill("BaronKill", "A2", t=1490.0)],
        allies=[],
    )
    assert rule_objective_taken_by_ally(snap) is None


# ---------------------------------------------------------------------------
# Tier — Baron / Elder / Soul / regular drake / Herald
# ---------------------------------------------------------------------------

def test_baron_kill_fires_alert() -> None:
    snap = _Snap(
        game_time=1500.0,
        raw_events=[_objective_kill("BaronKill", "A2", t=1490.0)],
        allies=ALLIES, enemies=ENEMIES,
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "objective_taken_baron"
    assert "BARON" in rec.text
    assert "Inhib" in rec.text


def test_elder_kill_fires_alert() -> None:
    """Elder dragon — explicit dragon_type field set to 'Elder'."""
    snap = _Snap(
        game_time=2100.0,
        raw_events=[_objective_kill("DragonKill", "A3", t=2090.0,
                                    dragon_type="Elder")],
        allies=ALLIES, enemies=ENEMIES,
        ally_aggregate=_AllyAggregate(dragons=4),
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "objective_taken_elder"
    assert "ELDER" in rec.text
    assert "INHIB" in rec.text


def test_soul_drake_kill_fires_alert() -> None:
    """4th drake claim by ally team = soul."""
    snap = _Snap(
        game_time=1300.0,
        raw_events=[_objective_kill("DragonKill", "A2", t=1290.0,
                                    dragon_type="Infernal")],
        allies=ALLIES, enemies=ENEMIES,
        ally_aggregate=_AllyAggregate(dragons=4),
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "objective_taken_soul"
    assert "SOUL" in rec.text


def test_regular_drake_kill_fires_info() -> None:
    """First or second drake — info severity."""
    snap = _Snap(
        game_time=600.0,
        raw_events=[_objective_kill("DragonKill", "A2", t=590.0,
                                    dragon_type="Cloud")],
        allies=ALLIES, enemies=ENEMIES,
        ally_aggregate=_AllyAggregate(dragons=1),
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "objective_taken_drake"
    assert "Drache" in rec.text


def test_herald_kill_fires_info() -> None:
    snap = _Snap(
        game_time=900.0,
        raw_events=[_objective_kill("HeraldKill", "A2", t=890.0)],
        allies=ALLIES, enemies=ENEMIES,
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "objective_taken_herald"
    assert "Herald" in rec.text


# ---------------------------------------------------------------------------
# Hysteresis — fires once per kill instance
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_kill() -> None:
    snap = _Snap(
        game_time=1500.0,
        raw_events=[_objective_kill("BaronKill", "A2", t=1490.0)],
        allies=ALLIES, enemies=ENEMIES,
    )
    first = rule_objective_taken_by_ally(snap)
    second = rule_objective_taken_by_ally(snap)
    assert first is not None
    assert second is None


def test_separate_kills_both_fire() -> None:
    """Different objectives at different times — each gets its own rec."""
    # Drake at 600s.
    snap_drake = _Snap(
        game_time=600.0,
        raw_events=[_objective_kill("DragonKill", "A2", t=590.0,
                                    dragon_type="Cloud")],
        allies=ALLIES, enemies=ENEMIES,
        ally_aggregate=_AllyAggregate(dragons=1),
    )
    rec_drake = rule_objective_taken_by_ally(snap_drake)
    assert rec_drake is not None

    # Baron at 1500s.
    snap_baron = _Snap(
        game_time=1500.0,
        raw_events=[
            _objective_kill("DragonKill", "A2", t=590.0, dragon_type="Cloud"),
            _objective_kill("BaronKill", "A3", t=1490.0),
        ],
        allies=ALLIES, enemies=ENEMIES,
        ally_aggregate=_AllyAggregate(dragons=1, barons=1),
    )
    rec_baron = rule_objective_taken_by_ally(snap_baron)
    assert rec_baron is not None
    assert rec_baron.kind == "objective_taken_baron"


def test_picks_most_recent_kill() -> None:
    """If multiple ally kills are in the recent window, pick the latest one."""
    snap = _Snap(
        game_time=1500.0,
        raw_events=[
            _objective_kill("HeraldKill", "A2", t=1485.0),
            _objective_kill("BaronKill", "A3", t=1495.0),  # later
        ],
        allies=ALLIES, enemies=ENEMIES,
    )
    rec = rule_objective_taken_by_ally(snap)
    assert rec is not None
    assert rec.kind == "objective_taken_baron"


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "alert") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="objective",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_baron_taken_suppressed_by_ace() -> None:
    """ace already implies map-state windfall — redundant."""
    recs = [_rec("ace", "alert"), _rec("objective_taken_baron", "alert")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_taken_baron" for r in out)


def test_baron_taken_survives_numbers_disadv() -> None:
    """Baron buff + 4v5: super minions still siege effectively. Keep firing."""
    recs = [_rec("numbers_disadv", "warn"),
            _rec("objective_taken_baron", "alert")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "objective_taken_baron" for r in out)


def test_baron_taken_suppressed_by_ally_inhib_down() -> None:
    """Defending base trumps post-baron conversion."""
    recs = [_rec("ally_inhib_down", "alert"),
            _rec("objective_taken_baron", "alert")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_taken_baron" for r in out)


def test_drake_taken_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("objective_taken_drake", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_taken_drake" for r in out)


def test_soul_taken_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("objective_taken_soul", "alert")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "objective_taken_soul" for r in out)


def test_herald_taken_survives_normal_tilt() -> None:
    recs = [_rec("tilt", "warn"), _rec("objective_taken_herald", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "objective_taken_herald" for r in out)
