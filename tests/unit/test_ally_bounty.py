"""Tests for rule_ally_bounty — protect-the-carry coaching (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    BOUNTY_TIER_GODLIKE_S,
    BOUNTY_TIER_INFO_S,
    BOUNTY_TIER_WARN_S,
    Recommendation,
    _suppress_dominated,
    reset_ally_bounty_hysteresis,
    rule_ally_bounty,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_ally_bounty_hysteresis()
    yield
    reset_ally_bounty_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

ACTIVE = "Me"
ACTIVE_CHAMP = "Yasuo"


@dataclass
class _Ally:
    champion_name: str = "Jinx"
    summoner_name: str = "JinxAlly"
    deaths: int = 0
    position: str = "BOTTOM"
    respawn_timer: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self.respawn_timer <= 0.0


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


def _kill_event(killer: str, victim: str = "Enemy", t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


def _snap_with_ally_streak(ally_name: str, streak: int, *, position: str = "BOTTOM",
                           ally_summoner: str | None = None,
                           deaths: int = 0) -> _Snap:
    """Build a snapshot where an ally has ``streak`` consecutive kills."""
    summoner = ally_summoner or f"{ally_name}Player"
    events = [_kill_event(killer=ally_name, t=50.0 + i * 30.0) for i in range(streak)]
    # Active player is also in the allies list — make them distinguishable.
    me = _Ally(champion_name=ACTIVE_CHAMP, summoner_name=ACTIVE, position="MIDDLE")
    fed_ally = _Ally(champion_name=ally_name, summoner_name=summoner,
                     position=position, deaths=deaths)
    return _Snap(raw_events=events, allies=[me, fed_ally])


# ---------------------------------------------------------------------------
# Below threshold + missing data
# ---------------------------------------------------------------------------

def test_silent_when_no_allies() -> None:
    assert rule_ally_bounty(_Snap(allies=[])) is None


def test_silent_below_streak_threshold() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Jinx", 2))
    assert rec is None


def test_silent_when_only_active_player_has_streak() -> None:
    """Active player's bounty is handled by rule_active_bounty, not this one."""
    events = [_kill_event(killer=ACTIVE, t=50.0 + i * 30.0) for i in range(5)]
    snap = _Snap(
        raw_events=events,
        allies=[_Ally(champion_name=ACTIVE_CHAMP, summoner_name=ACTIVE)],
    )
    assert rule_ally_bounty(snap) is None


def test_silent_when_ally_dead() -> None:
    snap = _snap_with_ally_streak("Jinx", 5)
    snap.allies[1].respawn_timer = 30.0
    assert rule_ally_bounty(snap) is None


# ---------------------------------------------------------------------------
# Tier firing + protect-the-carry messaging
# ---------------------------------------------------------------------------

def test_killing_spree_fires_info() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Jinx", BOUNTY_TIER_INFO_S))
    assert rec is not None
    assert rec.kind == "ally_bounty"
    assert rec.severity == "info"
    assert "Jinx" in rec.text
    assert "Killing Spree" in rec.text


def test_unstoppable_fires_warn() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Yasuo", BOUNTY_TIER_WARN_S,
                                                 position="TOP"))
    assert rec is not None
    assert rec.severity == "warn"
    assert "UNSTOPPABLE" in rec.text
    assert "Protect-the-Carry" in rec.text


def test_godlike_fires_warn_with_win_condition_text() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Akali", BOUNTY_TIER_GODLIKE_S,
                                                 position="MIDDLE"))
    assert rec is not None
    assert rec.severity == "warn"
    assert "GODLIKE" in rec.text
    assert "Win-Condition" in rec.text


# ---------------------------------------------------------------------------
# Position-aware advice
# ---------------------------------------------------------------------------

def test_top_advice_mentions_tp() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Sett", 3, position="TOP"))
    assert rec is not None
    assert "TP" in rec.text or "Top-Side" in rec.text


def test_jungle_advice_mentions_jungle() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Vi", 3, position="JUNGLE"))
    assert rec is not None
    # Jungle protect = ward jungle paths
    assert "Jungle" in rec.text or "river" in rec.text.lower() or "Buffs" in rec.text


def test_mid_advice_mentions_roams() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Ahri", 3, position="MIDDLE"))
    assert rec is not None
    assert "Roams" in rec.text or "Mid" in rec.text


def test_bot_advice_mentions_drakes() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Jinx", 3, position="BOTTOM"))
    assert rec is not None
    assert "Drache" in rec.text or "Bot" in rec.text


def test_utility_advice_mentions_engages() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Thresh", 3, position="UTILITY"))
    assert rec is not None
    assert "Engages" in rec.text or "Bot" in rec.text


def test_unknown_position_uses_generic_fallback() -> None:
    rec = rule_ally_bounty(_snap_with_ally_streak("Foo", 3, position=""))
    assert rec is not None
    assert "Carry" in rec.text


# ---------------------------------------------------------------------------
# Per-ally hysteresis
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_tier() -> None:
    snap = _snap_with_ally_streak("Jinx", 3)
    first = rule_ally_bounty(snap)
    second = rule_ally_bounty(snap)
    assert first is not None
    assert second is None


def test_escalates_through_tiers() -> None:
    s3 = rule_ally_bounty(_snap_with_ally_streak("Jinx", 3))
    s5 = rule_ally_bounty(_snap_with_ally_streak("Jinx", 5))
    s7 = rule_ally_bounty(_snap_with_ally_streak("Jinx", 7))
    assert s3 is not None and "Killing Spree" in s3.text
    assert s5 is not None and "UNSTOPPABLE" in s5.text
    assert s7 is not None and "GODLIKE" in s7.text


def test_death_resets_per_ally() -> None:
    rule_ally_bounty(_snap_with_ally_streak("Jinx", 3, deaths=0))
    rec = rule_ally_bounty(_snap_with_ally_streak("Jinx", 3, deaths=1))
    assert rec is not None
    assert "Jinx" in rec.text


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_ally_bounty_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("ally_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "ally_bounty" for r in out)


def test_ally_bounty_suppressed_by_numbers_disadv() -> None:
    recs = [_rec("numbers_disadv", "warn"), _rec("ally_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "ally_bounty" for r in out)


def test_ally_bounty_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("ally_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "ally_bounty" for r in out)


def test_ally_bounty_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("ally_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "ally_bounty" for r in out)


def test_ally_bounty_suppressed_by_enemy_elder() -> None:
    recs = [_rec("enemy_elder_buff", "alert"), _rec("ally_bounty", "warn")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "ally_bounty" for r in out)


def test_ally_bounty_survives_normal_tilt() -> None:
    recs = [_rec("tilt", "warn"), _rec("ally_bounty", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "ally_bounty" for r in out)
