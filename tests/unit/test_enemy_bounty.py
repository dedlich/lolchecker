"""Tests for rule_enemy_bounty — proactive focus-call coaching (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    BOUNTY_TIER_GODLIKE_S,
    BOUNTY_TIER_INFO_S,
    BOUNTY_TIER_WARN_S,
    Recommendation,
    _suppress_dominated,
    reset_enemy_bounty_hysteresis,
    rule_enemy_bounty,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_enemy_bounty_hysteresis()
    yield
    reset_enemy_bounty_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Enemy:
    champion_name: str = "Jinx"
    summoner_name: str = "JinxPlayer"
    deaths: int = 0
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
    active_summoner: str = "Me"
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


def _kill_event(killer: str, victim: str = "OtherEnemy", t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


def _snap_with_streak(enemy_name: str, streak: int, *, deaths: int = 0,
                     extra_enemies: list | None = None) -> _Snap:
    """Build a snapshot where ``enemy_name`` has ``streak`` consecutive
    kills + no deaths since."""
    events = [_kill_event(killer=enemy_name, t=50.0 + i * 30.0)
              for i in range(streak)]
    enemies = [_Enemy(champion_name=enemy_name, summoner_name=enemy_name,
                      deaths=deaths)]
    if extra_enemies:
        enemies.extend(extra_enemies)
    return _Snap(raw_events=events, enemies=enemies)


# ---------------------------------------------------------------------------
# Below-threshold + missing-data
# ---------------------------------------------------------------------------

def test_silent_when_no_enemies() -> None:
    assert rule_enemy_bounty(_Snap()) is None


def test_silent_below_streak_threshold() -> None:
    """2-kill streak — Riot's bounty starts at 3."""
    rec = rule_enemy_bounty(_snap_with_streak("Jinx", 2))
    assert rec is None


def test_silent_when_enemy_is_dead() -> None:
    """Dead enemies aren't focusable — no point announcing their bounty."""
    snap = _snap_with_streak("Jinx", 5)
    snap.enemies[0].respawn_timer = 30.0
    assert rule_enemy_bounty(snap) is None


def test_silent_when_no_champion_name() -> None:
    snap = _Snap(
        raw_events=[_kill_event(killer="Foo")],
        enemies=[_Enemy(champion_name="", summoner_name="Foo")],
    )
    assert rule_enemy_bounty(snap) is None


# ---------------------------------------------------------------------------
# Tier firing
# ---------------------------------------------------------------------------

def test_killing_spree_fires_info() -> None:
    rec = rule_enemy_bounty(_snap_with_streak("Jinx", BOUNTY_TIER_INFO_S))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "enemy_bounty"
    assert "Jinx" in rec.text
    assert "150" in rec.text


def test_unstoppable_fires_warn() -> None:
    rec = rule_enemy_bounty(_snap_with_streak("Yasuo", BOUNTY_TIER_WARN_S))
    assert rec is not None
    assert rec.severity == "warn"
    assert "Yasuo" in rec.text
    assert "300" in rec.text


def test_godlike_fires_warn() -> None:
    rec = rule_enemy_bounty(_snap_with_streak("Akali", BOUNTY_TIER_GODLIKE_S))
    assert rec is not None
    assert rec.severity == "warn"
    assert "Akali" in rec.text
    assert "GODLIKE" in rec.text
    assert "500" in rec.text


# ---------------------------------------------------------------------------
# Per-enemy hysteresis
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_enemy_same_tier() -> None:
    snap = _snap_with_streak("Jinx", 3)
    first = rule_enemy_bounty(snap)
    second = rule_enemy_bounty(snap)
    assert first is not None
    assert second is None


def test_escalates_per_enemy() -> None:
    """3 → 5 → 7 streak escalation should fire each new tier once for one enemy."""
    s3 = rule_enemy_bounty(_snap_with_streak("Jinx", 3))
    s5 = rule_enemy_bounty(_snap_with_streak("Jinx", 5))
    s7 = rule_enemy_bounty(_snap_with_streak("Jinx", 7))
    assert s3 is not None and "Killing Spree" in s3.text
    assert s5 is not None and "UNSTOPPABLE" in s5.text
    assert s7 is not None and "GODLIKE" in s7.text


def test_different_enemies_track_separately() -> None:
    """Jinx hits 5, then Yasuo hits 3 — Yasuo's announcement fires."""
    rule_enemy_bounty(_snap_with_streak("Jinx", 5))
    # Now build a snapshot with both Jinx (5) AND Yasuo (3)
    events = (
        [_kill_event(killer="Jinx", t=50.0 + i * 30.0) for i in range(5)] +
        [_kill_event(killer="Yasuo", t=300.0 + i * 30.0) for i in range(3)]
    )
    snap = _Snap(
        raw_events=events,
        enemies=[
            _Enemy(champion_name="Jinx", summoner_name="Jinx"),
            _Enemy(champion_name="Yasuo", summoner_name="Yasuo"),
        ],
    )
    rec = rule_enemy_bounty(snap)
    assert rec is not None
    assert "Yasuo" in rec.text  # Jinx already announced; Yasuo is new


def test_death_resets_per_enemy() -> None:
    """Jinx dies — her next streak should re-fire from Killing Spree."""
    rule_enemy_bounty(_snap_with_streak("Jinx", 3, deaths=0))
    # Jinx died (deaths=1), gets a new 3-streak — should re-fire.
    rec = rule_enemy_bounty(_snap_with_streak("Jinx", 3, deaths=1))
    assert rec is not None
    assert "Jinx" in rec.text


def test_picks_highest_streak_when_multiple() -> None:
    """Jinx 7-streak and Yasuo 3-streak in same tick → fire for Jinx."""
    events = (
        [_kill_event(killer="Jinx", t=50.0 + i * 30.0) for i in range(7)] +
        [_kill_event(killer="Yasuo", t=300.0 + i * 30.0) for i in range(3)]
    )
    snap = _Snap(
        raw_events=events,
        enemies=[
            _Enemy(champion_name="Jinx", summoner_name="Jinx"),
            _Enemy(champion_name="Yasuo", summoner_name="Yasuo"),
        ],
    )
    rec = rule_enemy_bounty(snap)
    assert rec is not None
    assert "Jinx" in rec.text  # higher tier wins
    assert "GODLIKE" in rec.text


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_enemy_bounty_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("enemy_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_bounty" for r in out)


def test_enemy_bounty_suppressed_by_numbers_disadv() -> None:
    """Don't tell short-handed players to focus anyone — they shouldn't fight."""
    recs = [_rec("numbers_disadv", "warn"), _rec("enemy_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_bounty" for r in out)


def test_enemy_bounty_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("enemy_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_bounty" for r in out)


def test_enemy_bounty_suppressed_by_spiral_tilt() -> None:
    """Feeding player should not fight, period."""
    recs = [_rec("tilt", "alert"), _rec("enemy_bounty", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_bounty" for r in out)


def test_enemy_bounty_suppressed_by_enemy_elder() -> None:
    """Enemy execute = no fight."""
    recs = [_rec("enemy_elder_buff", "alert"), _rec("enemy_bounty", "warn")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "enemy_bounty" for r in out)


def test_enemy_bounty_survives_normal_tilt() -> None:
    """Non-spiral tilt + enemy bounty: focus call still actionable."""
    recs = [_rec("tilt", "warn"), _rec("enemy_bounty", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "enemy_bounty" for r in out)
