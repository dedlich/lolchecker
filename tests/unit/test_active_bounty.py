"""Tests for rule_active_bounty — proactive bounty awareness (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    BOUNTY_TIER_GODLIKE_S,
    BOUNTY_TIER_INFO_S,
    BOUNTY_TIER_WARN_S,
    Recommendation,
    _suppress_dominated,
    reset_bounty_hysteresis,
    rule_active_bounty,
)


@pytest.fixture(autouse=True)
def _reset_bounty():
    """Bounty rule keeps process-wide tier state to fire once per
    escalation. Tests must each start with a clean slate."""
    reset_bounty_hysteresis()
    yield
    reset_bounty_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

ACTIVE = "Me"
ACTIVE_CHAMP = "Yasuo"
ENEMY = "Enemy"


@dataclass
class _Player:
    summoner_name: str = ACTIVE
    champion_name: str = ACTIVE_CHAMP
    deaths: int = 0
    position: str = "MIDDLE"
    team: str = "ORDER"


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


def _kill(killer: str, victim: str = ENEMY, t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


def _snap_with_streak(streak: int, *, deaths: int = 0,
                     game_time: float = 600.0) -> _Snap:
    """Build a snapshot whose raw_events contain ``streak`` consecutive
    kills by the active player and no deaths since."""
    events = [_kill(killer=ACTIVE, t=100.0 + i * 30.0) for i in range(streak)]
    return _Snap(
        game_time=game_time,
        raw_events=events,
        allies=[_Player(deaths=deaths)],
    )


# ---------------------------------------------------------------------------
# Below-threshold and missing-data
# ---------------------------------------------------------------------------

def test_silent_below_streak_threshold() -> None:
    """2-kill streak doesn't trigger bounty (Riot's threshold is 3)."""
    rec = rule_active_bounty(_snap_with_streak(2))
    assert rec is None


def test_silent_when_no_active_player() -> None:
    snap = _Snap(allies=[])
    assert rule_active_bounty(snap) is None


# ---------------------------------------------------------------------------
# Tier firing
# ---------------------------------------------------------------------------

def test_killing_spree_tier_fires_info() -> None:
    rec = rule_active_bounty(_snap_with_streak(BOUNTY_TIER_INFO_S))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "active_bounty"
    assert "Killing Spree" in rec.text
    assert "150" in rec.text


def test_unstoppable_tier_fires_warn() -> None:
    rec = rule_active_bounty(_snap_with_streak(BOUNTY_TIER_WARN_S))
    assert rec is not None
    assert rec.severity == "warn"
    assert "UNSTOPPABLE" in rec.text
    assert "300" in rec.text


def test_godlike_tier_fires_warn() -> None:
    rec = rule_active_bounty(_snap_with_streak(BOUNTY_TIER_GODLIKE_S))
    assert rec is not None
    assert rec.severity == "warn"
    assert "GODLIKE" in rec.text
    assert "500" in rec.text


def test_legendary_streak_uses_godlike_tier() -> None:
    """8+, 9+, 10+ all stay at the +500g cap."""
    rec = rule_active_bounty(_snap_with_streak(10))
    assert rec is not None
    assert "GODLIKE" in rec.text


# ---------------------------------------------------------------------------
# Hysteresis — fires once per tier per life
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_tier() -> None:
    """Two ticks at the same streak tier — only the first fires."""
    snap = _snap_with_streak(3)
    first = rule_active_bounty(snap)
    second = rule_active_bounty(snap)
    assert first is not None
    assert second is None


def test_escalates_through_tiers_in_one_life() -> None:
    """3 → 5 → 7 streak escalation should fire each new tier once."""
    rec3 = rule_active_bounty(_snap_with_streak(3))
    rec5 = rule_active_bounty(_snap_with_streak(5))
    rec7 = rule_active_bounty(_snap_with_streak(7))
    assert rec3 is not None and "Killing Spree" in rec3.text
    assert rec5 is not None and "UNSTOPPABLE" in rec5.text
    assert rec7 is not None and "GODLIKE" in rec7.text


def test_does_not_drop_back_to_lower_tier() -> None:
    """Once we've fired UNSTOPPABLE, dropping back to a 4-streak (e.g. via
    state corruption) shouldn't re-fire Killing Spree."""
    rule_active_bounty(_snap_with_streak(5))  # arms UNSTOPPABLE
    # Simulate a snapshot that somehow only has 4 events (shouldn't happen
    # in practice without a death, but defensive).
    rec = rule_active_bounty(_snap_with_streak(4))
    assert rec is None


def test_death_resets_hysteresis() -> None:
    """After dying (deaths counter increments), the next streak earns
    new tier announcements."""
    # First life: bounty at streak 3 fires.
    first = rule_active_bounty(_snap_with_streak(3, deaths=0))
    assert first is not None
    # Player dies, deaths=1 — bounty wiped, next streak should re-fire.
    second = rule_active_bounty(_snap_with_streak(3, deaths=1))
    assert second is not None


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="safety",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_bounty_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("active_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "active_bounty" for r in out)


def test_bounty_suppressed_by_numbers_disadv() -> None:
    """The 'don't fight' call is more urgent than 'you have bounty'."""
    recs = [_rec("numbers_disadv", "warn"), _rec("active_bounty", "warn")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "active_bounty" for r in out)


def test_bounty_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("active_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "active_bounty" for r in out)


def test_bounty_survives_normal_tilt() -> None:
    """Non-spiral tilt + bounty: shouldn't happen often (spiral and streak
    are mutually exclusive) but if both fire, bounty info is still useful."""
    recs = [_rec("tilt", "warn"), _rec("active_bounty", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "active_bounty" for r in out)
