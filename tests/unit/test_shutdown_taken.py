"""Tests for rule_shutdown_taken — bountied-enemy-died conversion call (B5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    BOUNTY_TIER_GODLIKE_S,
    BOUNTY_TIER_INFO_S,
    BOUNTY_TIER_WARN_S,
    Recommendation,
    _suppress_dominated,
    reset_shutdown_taken_hysteresis,
    rule_shutdown_taken,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_shutdown_taken_hysteresis()
    yield
    reset_shutdown_taken_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Enemy:
    champion_name: str = "Jinx"
    summoner_name: str = "Jinx"
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


def _kill(killer: str, victim: str = "Other", t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": [],
    }


def _alive_with_streak(name: str, streak: int) -> tuple[_Enemy, list[dict]]:
    """Build an alive enemy + the kill events that give them ``streak``
    consecutive kills."""
    enemy = _Enemy(champion_name=name, summoner_name=name, deaths=0)
    events = [_kill(killer=name, victim=f"Other{i}", t=100.0 + i * 30.0)
              for i in range(streak)]
    return enemy, events


def _dead_after_streak(name: str, streak: int, *, deaths: int = 1
                       ) -> tuple[_Enemy, list[dict]]:
    """Build a dead enemy whose pre-death streak was ``streak``.
    The death event included resets _kill_streak to 0, mirroring real LCDA."""
    enemy = _Enemy(champion_name=name, summoner_name=name,
                   deaths=deaths, respawn_timer=30.0)
    events = [_kill(killer=name, victim=f"Other{i}", t=100.0 + i * 30.0)
              for i in range(streak)]
    # Append the death event that reset the streak.
    events.append(_kill(killer="Hunter", victim=name,
                        t=100.0 + streak * 30.0 + 5.0))
    return enemy, events


# ---------------------------------------------------------------------------
# Two-tick tracking — must observe alive tier before death tick
# ---------------------------------------------------------------------------

def test_silent_when_no_enemies() -> None:
    assert rule_shutdown_taken(_Snap()) is None


def test_silent_when_enemy_alive_no_streak() -> None:
    """Alive enemy with 0 streak — no shutdown to announce."""
    enemy, events = _alive_with_streak("Jinx", 0)
    assert rule_shutdown_taken(_Snap(enemies=[enemy], raw_events=events)) is None


def test_silent_when_enemy_dies_without_prior_streak_tracked() -> None:
    """Cold-start: we never saw the enemy alive on a streak, so we
    can't claim a shutdown happened. (Real game: the rule is running
    every 2 s; LcdaSource keeps the engine warm.)"""
    enemy, events = _dead_after_streak("Jinx", 5)
    assert rule_shutdown_taken(_Snap(enemies=[enemy], raw_events=events)) is None


def test_alive_then_dead_fires_shutdown() -> None:
    """Tick 1: alive on streak, no rec. Tick 2: dead, rec fires."""
    # Tick 1 — alive with 5-streak.
    enemy_alive, events_alive = _alive_with_streak("Jinx", 5)
    rule_shutdown_taken(_Snap(enemies=[enemy_alive], raw_events=events_alive))

    # Tick 2 — same enemy now dead.
    enemy_dead, events_dead = _dead_after_streak("Jinx", 5)
    rec = rule_shutdown_taken(_Snap(enemies=[enemy_dead], raw_events=events_dead))
    assert rec is not None
    assert rec.kind == "shutdown_taken"
    assert "Jinx" in rec.text


# ---------------------------------------------------------------------------
# Tier mapping (info / warn / alert)
# ---------------------------------------------------------------------------

def _two_tick_fire(streak: int) -> Recommendation | None:
    """Helper: simulate the 2-tick alive→dead transition for a given streak."""
    enemy_alive, events_alive = _alive_with_streak("Jinx", streak)
    rule_shutdown_taken(_Snap(enemies=[enemy_alive], raw_events=events_alive))
    enemy_dead, events_dead = _dead_after_streak("Jinx", streak)
    return rule_shutdown_taken(_Snap(enemies=[enemy_dead], raw_events=events_dead))


def test_killing_spree_shutdown_fires_info() -> None:
    rec = _two_tick_fire(BOUNTY_TIER_INFO_S)
    assert rec is not None
    assert rec.severity == "info"
    assert "150g" in rec.text


def test_unstoppable_shutdown_fires_warn() -> None:
    rec = _two_tick_fire(BOUNTY_TIER_WARN_S)
    assert rec is not None
    assert rec.severity == "warn"
    assert "300g" in rec.text


def test_godlike_shutdown_fires_alert() -> None:
    rec = _two_tick_fire(BOUNTY_TIER_GODLIKE_S)
    assert rec is not None
    assert rec.severity == "alert"
    assert "500g" in rec.text
    assert "GAME-RESET" in rec.text


# ---------------------------------------------------------------------------
# Hysteresis
# ---------------------------------------------------------------------------

def test_does_not_re_fire_same_death() -> None:
    """Two ticks of the same dead enemy — fire once, not twice."""
    enemy_alive, events_alive = _alive_with_streak("Jinx", 5)
    rule_shutdown_taken(_Snap(enemies=[enemy_alive], raw_events=events_alive))
    enemy_dead, events_dead = _dead_after_streak("Jinx", 5)
    snap = _Snap(enemies=[enemy_dead], raw_events=events_dead)
    first = rule_shutdown_taken(snap)
    second = rule_shutdown_taken(snap)
    assert first is not None
    assert second is None


def test_re_fires_after_respawn_and_new_streak() -> None:
    """Jinx died on streak 3 → fire. Jinx respawns, gets new streak 5,
    dies again → fire again."""
    # First death cycle.
    enemy_alive_1, events_alive_1 = _alive_with_streak("Jinx", 3)
    rule_shutdown_taken(_Snap(enemies=[enemy_alive_1], raw_events=events_alive_1))
    enemy_dead_1, events_dead_1 = _dead_after_streak("Jinx", 3, deaths=1)
    rec1 = rule_shutdown_taken(_Snap(enemies=[enemy_dead_1],
                                     raw_events=events_dead_1))
    assert rec1 is not None

    # Jinx respawns, builds 5-streak (deaths still 1).
    events_round2 = events_dead_1 + [
        _kill(killer="Jinx", victim=f"X{i}", t=300.0 + i * 30.0) for i in range(5)
    ]
    enemy_alive_2 = _Enemy(champion_name="Jinx", summoner_name="Jinx", deaths=1)
    rule_shutdown_taken(_Snap(enemies=[enemy_alive_2], raw_events=events_round2))

    # Second death cycle (deaths=2 now).
    events_dead_2 = events_round2 + [_kill(killer="Hunter", victim="Jinx", t=500.0)]
    enemy_dead_2 = _Enemy(champion_name="Jinx", summoner_name="Jinx",
                          deaths=2, respawn_timer=40.0)
    rec2 = rule_shutdown_taken(_Snap(enemies=[enemy_dead_2],
                                     raw_events=events_dead_2))
    assert rec2 is not None  # different death-instance, fires again


def test_picks_highest_tier_when_multiple_die() -> None:
    """Two enemies just died — pick the one with the higher pre-death tier."""
    # Tick 1 — both alive, different streaks.
    enemy_a_alive = _Enemy(champion_name="Jinx", summoner_name="Jinx", deaths=0)
    enemy_b_alive = _Enemy(champion_name="Yasuo", summoner_name="Yasuo", deaths=0)
    events = (
        [_kill(killer="Jinx", victim=f"X{i}", t=100.0 + i * 10.0) for i in range(7)] +
        [_kill(killer="Yasuo", victim=f"Y{i}", t=200.0 + i * 10.0) for i in range(3)]
    )
    rule_shutdown_taken(_Snap(enemies=[enemy_a_alive, enemy_b_alive],
                              raw_events=events))

    # Tick 2 — both die in the same tick.
    enemy_a_dead = _Enemy(champion_name="Jinx", summoner_name="Jinx",
                          deaths=1, respawn_timer=40.0)
    enemy_b_dead = _Enemy(champion_name="Yasuo", summoner_name="Yasuo",
                          deaths=1, respawn_timer=30.0)
    events_2 = events + [
        _kill(killer="Hunter", victim="Jinx", t=400.0),
        _kill(killer="Hunter", victim="Yasuo", t=400.5),
    ]
    rec = rule_shutdown_taken(_Snap(enemies=[enemy_a_dead, enemy_b_dead],
                                    raw_events=events_2))
    assert rec is not None
    assert "Jinx" in rec.text  # higher tier wins


# ---------------------------------------------------------------------------
# Phase-aware advice
# ---------------------------------------------------------------------------

def test_late_game_advice_mentions_baron_or_inhib() -> None:
    """At 28:00 a +500g shutdown should mention Baron or Inhib."""
    enemy_alive, events_alive = _alive_with_streak("Jinx", BOUNTY_TIER_GODLIKE_S)
    rule_shutdown_taken(_Snap(game_time=1700.0, enemies=[enemy_alive],
                              raw_events=events_alive))
    enemy_dead, events_dead = _dead_after_streak("Jinx", BOUNTY_TIER_GODLIKE_S)
    rec = rule_shutdown_taken(_Snap(game_time=1700.0, enemies=[enemy_dead],
                                    raw_events=events_dead))
    assert rec is not None
    assert "Baron" in rec.text or "Inhib" in rec.text


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("shutdown_taken", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "shutdown_taken" for r in out)


def test_suppressed_by_numbers_disadv() -> None:
    """Don't tell short-handed players to push for objectives."""
    recs = [_rec("numbers_disadv", "warn"), _rec("shutdown_taken", "warn")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "shutdown_taken" for r in out)


def test_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("shutdown_taken", "alert")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "shutdown_taken" for r in out)


def test_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("shutdown_taken", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "shutdown_taken" for r in out)


def test_survives_normal_tilt() -> None:
    recs = [_rec("tilt", "warn"), _rec("shutdown_taken", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "shutdown_taken" for r in out)
