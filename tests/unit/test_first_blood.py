"""Tests for rule_first_blood — single-fire FB momentum coaching (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    Recommendation,
    _suppress_dominated,
    reset_first_blood_hysteresis,
    rule_first_blood,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_first_blood_hysteresis()
    yield
    reset_first_blood_hysteresis()


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
    game_time: float = 200.0
    raw_events: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    active_team: str = "ORDER"
    active_summoner: str = ACTIVE
    active_level: int = 3
    active_items: int = 0
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


def _kill(killer: str, victim: str = "Other", t: float = 100.0,
          name: str = "ChampionKill") -> dict:
    return {
        "EventName": name,
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


def _allies(*champs: str, include_active: bool = True) -> list[_Player]:
    """Build an allies list. Active player is auto-included unless suppressed."""
    out: list[_Player] = []
    if include_active:
        out.append(_Player(summoner_name=ACTIVE, champion_name=ACTIVE_CHAMP))
    for c in champs:
        out.append(_Player(summoner_name=c, champion_name=c))
    return out


def _enemies(*champs: str) -> list[_Player]:
    return [_Player(summoner_name=c, champion_name=c) for c in champs]


# ---------------------------------------------------------------------------
# No-FB / no-data
# ---------------------------------------------------------------------------

def test_silent_when_no_events() -> None:
    snap = _Snap(allies=_allies(), enemies=_enemies("Yasuo"))
    assert rule_first_blood(snap) is None


def test_silent_when_no_champion_kill_yet() -> None:
    """Other event types don't count — only ChampionKill is FB."""
    snap = _Snap(
        raw_events=[_kill("AllyOne", t=100, name="DragonKill")],
        allies=_allies("AllyOne"),
        enemies=_enemies("Yasuo"),
    )
    assert rule_first_blood(snap) is None


def test_silent_when_killer_unknown() -> None:
    """Killer not in either team's id set → can't classify, no fire."""
    snap = _Snap(
        raw_events=[_kill("Stranger", t=100)],
        allies=_allies(),
        enemies=_enemies("Yasuo"),
    )
    assert rule_first_blood(snap) is None


# ---------------------------------------------------------------------------
# Three branches: active / ally / enemy got FB
# ---------------------------------------------------------------------------

def test_active_player_got_fb() -> None:
    snap = _Snap(
        raw_events=[_kill(ACTIVE, victim="Yasuo", t=180.0)],
        allies=_allies("AllyOne"),
        enemies=_enemies("Yasuo"),
    )
    rec = rule_first_blood(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "first_blood"
    assert "DU" in rec.text  # personal-momentum framing
    assert "400g" in rec.text


def test_ally_got_fb() -> None:
    snap = _Snap(
        raw_events=[_kill("AllyJg", victim="Yasuo", t=180.0)],
        allies=_allies("AllyJg"),
        enemies=_enemies("Yasuo"),
    )
    rec = rule_first_blood(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert "Team" in rec.text
    assert "AllyJg" in rec.text  # names the ally


def test_enemy_got_fb() -> None:
    snap = _Snap(
        raw_events=[_kill("EnemyJg", victim=ACTIVE, t=180.0)],
        allies=_allies(),
        enemies=_enemies("EnemyJg", "Yasuo"),
    )
    rec = rule_first_blood(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert "Gegner" in rec.text
    assert "defensiv" in rec.text


def test_picks_first_kill_chronologically() -> None:
    """Multiple kills in the events list — only the earliest counts as FB."""
    snap = _Snap(
        raw_events=[
            _kill("EnemyJg", victim=ACTIVE, t=200.0),
            _kill("AllyMid", victim="Yasuo", t=180.0),  # earlier — this is FB
            _kill(ACTIVE, victim="Ahri", t=300.0),
        ],
        allies=_allies("AllyMid"),
        enemies=_enemies("EnemyJg", "Yasuo", "Ahri"),
    )
    rec = rule_first_blood(snap)
    assert rec is not None
    assert "AllyMid" in rec.text  # earliest kill wins


# ---------------------------------------------------------------------------
# Hysteresis — fires once per game
# ---------------------------------------------------------------------------

def test_does_not_re_fire_after_first() -> None:
    snap = _Snap(
        raw_events=[_kill(ACTIVE, victim="Yasuo", t=180.0)],
        allies=_allies(),
        enemies=_enemies("Yasuo"),
    )
    first = rule_first_blood(snap)
    second = rule_first_blood(snap)
    assert first is not None
    assert second is None


def test_unknown_killer_does_not_consume_hysteresis() -> None:
    """If the killer can't be identified, the rule re-arms — a later
    identifiable FB should still fire."""
    snap_unknown = _Snap(
        raw_events=[_kill("Stranger", t=180.0)],
        allies=_allies(),
        enemies=_enemies("Yasuo"),
    )
    rule_first_blood(snap_unknown)  # should NOT consume hysteresis
    # Now a real FB event arrives.
    snap_real = _Snap(
        raw_events=[_kill(ACTIVE, victim="Yasuo", t=200.0)],
        allies=_allies(),
        enemies=_enemies("Yasuo"),
    )
    rec = rule_first_blood(snap_real)
    assert rec is not None


def test_reset_re_arms_after_new_game() -> None:
    snap = _Snap(
        raw_events=[_kill(ACTIVE, victim="Yasuo", t=180.0)],
        allies=_allies(),
        enemies=_enemies("Yasuo"),
    )
    rule_first_blood(snap)
    reset_first_blood_hysteresis()
    rec = rule_first_blood(snap)
    assert rec is not None


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("first_blood", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "first_blood" for r in out)


def test_suppressed_by_numbers_disadv() -> None:
    recs = [_rec("numbers_disadv", "warn"), _rec("first_blood", "warn")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "first_blood" for r in out)


def test_survives_normal_tilt() -> None:
    """Tilt + FB can coexist — different timeframes."""
    recs = [_rec("tilt", "warn"), _rec("first_blood", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "first_blood" for r in out)
