"""Tests for the deterministic jungle camp predictor."""
from __future__ import annotations

import math

from champ_assistant.jungle_timeline import (
    ALIVE_GRACE_S,
    JUNGLE_CAMPS,
    CampSpec,
    CampState,
    JungleTimelineEngine,
    _camp_state_at,
)


# --------------------------------------------------------------------------
# Pure cycle math (no engine instance needed)
# --------------------------------------------------------------------------
RED = CampSpec("red_buff", "Red Buff", 90.0, 300.0)
GROMP = CampSpec("gromp", "Gromp", 90.0, 135.0)
SCUTTLE = CampSpec("scuttle", "Scuttle", 195.0, 150.0)


def test_pre_first_spawn_counts_down_to_first() -> None:
    s = _camp_state_at(RED, game_time=0.0, confidence=1.0)
    assert s.state == "respawning"
    assert s.next_spawn_at == 90.0
    assert s.time_remaining == 90.0


def test_at_first_spawn_camp_is_alive_briefly() -> None:
    s = _camp_state_at(RED, game_time=90.0, confidence=1.0)
    assert s.state == "alive"
    assert s.next_spawn_at == 390.0
    assert s.time_remaining == 300.0


def test_alive_grace_window_ends_then_respawning() -> None:
    s_inside = _camp_state_at(RED, game_time=90.0 + ALIVE_GRACE_S - 0.1, confidence=1.0)
    assert s_inside.state == "alive"
    s_outside = _camp_state_at(RED, game_time=90.0 + ALIVE_GRACE_S + 0.1, confidence=1.0)
    assert s_outside.state == "respawning"


def test_red_buff_cycle_matches_canonical_5min() -> None:
    # First kill assumed at 1:30, so respawn at 6:30 (390s).
    s = _camp_state_at(RED, game_time=200.0, confidence=1.0)
    assert s.next_spawn_at == 390.0
    assert s.time_remaining == 190.0


def test_red_buff_second_cycle() -> None:
    # After 6:30 spawn (assumed kill), next spawn at 11:30 (690s).
    s = _camp_state_at(RED, game_time=500.0, confidence=1.0)
    assert s.next_spawn_at == 690.0
    assert s.time_remaining == 190.0


def test_gromp_cycle_matches_135s() -> None:
    # First spawn 90s. Cycles: 90, 225, 360, 495, ...
    s = _camp_state_at(GROMP, game_time=300.0, confidence=1.0)
    assert s.next_spawn_at == 360.0
    assert s.time_remaining == 60.0


def test_scuttle_first_spawn_is_3min15() -> None:
    s = _camp_state_at(SCUTTLE, game_time=100.0, confidence=1.0)
    # Pre-first-spawn — counts down to 195s.
    assert s.state == "respawning"
    assert s.next_spawn_at == 195.0
    assert s.time_remaining == 95.0


def test_scuttle_after_first_spawn_cycles_at_2min30() -> None:
    # 199s is within the 5s alive-grace window (spawn at 195s).
    s = _camp_state_at(SCUTTLE, game_time=199.0, confidence=1.0)
    assert s.state == "alive"
    assert s.next_spawn_at == 345.0  # 195 + 150


# --------------------------------------------------------------------------
# Engine lifecycle
# --------------------------------------------------------------------------
def test_engine_states_before_init_are_safe() -> None:
    engine = JungleTimelineEngine()
    assert not engine.is_initialized
    states = engine.states()
    # Returns a state per camp — safe defaults, confidence 0.
    assert len(states) == len(JUNGLE_CAMPS)
    for s in states.values():
        assert s.confidence == 0.0
        assert s.time_remaining >= 0
        assert s.state in ("alive", "respawning")


def test_engine_initialize_sets_baseline() -> None:
    engine = JungleTimelineEngine()
    engine.initialize(game_time=120.0)
    assert engine.is_initialized
    assert engine.confidence > 0.5


def test_engine_initialize_is_idempotent() -> None:
    engine = JungleTimelineEngine()
    engine.initialize(0.0)
    # Push some events that would bump anchor_count.
    engine.tick(60.0, [{"EventID": 1, "EventName": "DragonKill"}])
    boosted = engine.confidence
    # Re-initialize must NOT reset accumulated anchors.
    engine.initialize(0.0)
    assert engine.confidence == boosted


def test_engine_auto_initializes_on_first_tick() -> None:
    """Useful for the 'attached overlay mid-match' case where GameStart
    event was already in the past and won't fire again."""
    engine = JungleTimelineEngine()
    engine.tick(420.0)
    assert engine.is_initialized
    assert engine.confidence > 0


# --------------------------------------------------------------------------
# tick() behavior
# --------------------------------------------------------------------------
def test_tick_returns_full_camp_set() -> None:
    engine = JungleTimelineEngine()
    states = engine.tick(150.0)
    assert set(states.keys()) == {spec.id for spec in JUNGLE_CAMPS}


def test_tick_with_negative_game_time_keeps_last_state() -> None:
    engine = JungleTimelineEngine()
    engine.tick(60.0)
    snapshot_before = engine.states()
    # Bogus input — engine must not crash, must not advance.
    result = engine.tick(-5.0)
    assert {s.next_spawn_at for s in result.values()} == {
        s.next_spawn_at for s in snapshot_before.values()
    }


def test_tick_with_nan_is_rejected() -> None:
    engine = JungleTimelineEngine()
    engine.tick(60.0)
    snapshot_before = engine.states()
    result = engine.tick(math.nan)
    assert {s.next_spawn_at for s in result.values()} == {
        s.next_spawn_at for s in snapshot_before.values()
    }


def test_tick_notifies_subscribers() -> None:
    engine = JungleTimelineEngine()
    received: list[dict] = []
    engine.subscribe(received.append)
    engine.tick(120.0)
    engine.tick(140.0)
    assert len(received) == 2
    assert "order_red_buff" in received[0]


# --------------------------------------------------------------------------
# Observed-clear gating — engine no longer emits predictive timers
# --------------------------------------------------------------------------
def test_unanchored_camps_return_alive_sentinel() -> None:
    """User rejected predictive 'pseudo' timers — without an observed
    clear, the engine should NOT emit a respawn countdown. Camps stay
    in the 'alive' sentinel so the UI's skip-if-alive paint path
    naturally hides them."""
    engine = JungleTimelineEngine()
    engine.tick(180.0)  # 3 minutes in, nothing observed yet
    states = engine.states()
    for state in states.values():
        assert state.state == "alive"
        assert state.time_remaining == 0.0
        assert state.next_spawn_at == 0.0


def test_anchored_camp_emits_real_countdown() -> None:
    """Once register_clear is called, the camp's real respawn cycle
    drives the timer — that's the trustworthy half of the engine."""
    engine = JungleTimelineEngine()
    engine.tick(120.0)
    engine.register_clear("order_red_buff", game_time=120.0)
    state = engine.states()["order_red_buff"]
    # Red Buff respawns 5:00 (300s) after kill → spawn at 420s.
    # At game_time=120, 300s remaining (some grace tolerance).
    assert state.next_spawn_at == 420.0
    assert 0.0 < state.time_remaining <= 300.0


def test_unrelated_camps_stay_hidden_after_one_anchor() -> None:
    """Registering a clear on order_red_buff must NOT also surface predictive
    timers for other camps. Each camp gates independently."""
    engine = JungleTimelineEngine()
    engine.tick(60.0)
    engine.register_clear("order_red_buff", game_time=60.0)
    states = engine.states()
    for camp_id, state in states.items():
        if camp_id == "order_red_buff":
            assert state.state == "respawning"
        else:
            assert state.state == "alive"


def test_unsubscribe_stops_notifications() -> None:
    engine = JungleTimelineEngine()
    received: list[dict] = []
    unsub = engine.subscribe(received.append)
    engine.tick(120.0)
    unsub()
    engine.tick(140.0)
    assert len(received) == 1


def test_failing_listener_does_not_block_others() -> None:
    engine = JungleTimelineEngine()
    received: list[dict] = []

    def boom(_states: dict) -> None:
        raise RuntimeError("boom")

    engine.subscribe(boom)
    engine.subscribe(received.append)
    engine.tick(120.0)
    assert len(received) == 1


# --------------------------------------------------------------------------
# Confidence model
# --------------------------------------------------------------------------
def test_confidence_decays_over_match_duration() -> None:
    engine = JungleTimelineEngine()
    engine.initialize(0.0)
    early = engine.confidence
    engine.tick(900.0)  # 15 minutes
    mid = engine.confidence
    engine.tick(1800.0)  # 30 minutes
    late = engine.confidence
    assert early > mid > late


def test_confidence_floored_at_min() -> None:
    from champ_assistant.jungle_timeline import MIN_CONFIDENCE
    engine = JungleTimelineEngine()
    engine.tick(99999.0)  # way past any reasonable match duration
    assert engine.confidence >= MIN_CONFIDENCE


def test_objective_kill_boosts_confidence() -> None:
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    before = engine.confidence
    engine.tick(610.0, events=[
        {"EventID": 5, "EventName": "DragonKill", "DragonType": "Cloud"},
    ])
    assert engine.confidence > before


def test_repeated_event_id_is_not_double_counted() -> None:
    """LCDA returns the cumulative event log on every poll — the same
    EventID arrives over and over. Must not bump anchor_count twice.
    Compared via the internal counter (raw confidence also decays with
    elapsed time, so it's not a stable equality target)."""
    engine = JungleTimelineEngine()
    engine.tick(600.0, events=[{"EventID": 5, "EventName": "DragonKill"}])
    anchors_after_first = engine._anchor_count
    engine.tick(605.0, events=[{"EventID": 5, "EventName": "DragonKill"}])
    assert engine._anchor_count == anchors_after_first


def test_unknown_event_types_ignored() -> None:
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    before = engine.confidence
    engine.tick(610.0, events=[
        {"EventID": 99, "EventName": "ChampionKill"},
        {"EventID": 100, "EventName": "MinionsSpawning"},
    ])
    # No anchor bump from those; only the time-decay effect.
    assert engine.confidence <= before


# --------------------------------------------------------------------------
# CampState contract for UI
# --------------------------------------------------------------------------
def test_camp_state_is_immutable() -> None:
    s = _camp_state_at(RED, game_time=0.0, confidence=1.0)
    try:
        s.time_remaining = 99  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("CampState should be frozen")


def test_time_remaining_is_never_negative() -> None:
    """Floating-point rounding could push time_remaining slightly below 0;
    UI should never have to defend against that."""
    engine = JungleTimelineEngine()
    for t in (0.0, 89.99, 90.0, 90.001, 389.99, 390.0, 390.001, 1234.5):
        states = engine.tick(t)
        for s in states.values():
            assert s.time_remaining >= 0, f"camp {s.id} had negative time at t={t}"
