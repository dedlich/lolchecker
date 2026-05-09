"""Tests for lane-opponent MIA detection (Charter B2 — lane side)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.lcda.lane_state import (
    LANE_PHASE_END_S,
    LANE_PHASE_START_S,
    MIA_INFO_S,
    MIA_WARN_S,
    LaneOpponentMia,
    detect_lane_opponent_mia,
    find_lane_opponent,
)


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Enemy:
    champion_name: str = "Yasuo"
    position: str = "MIDDLE"
    creep_score: int = 0
    respawn_timer: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self.respawn_timer <= 0.0


def _run(
    *,
    active_position: str = "MIDDLE",
    enemies: list | None = None,
    game_time: float = 300.0,
    prev_last_cs_at: dict | None = None,
    prev_cs: dict | None = None,
    prev_alive: dict | None = None,
    raw_events: list | None = None,
):
    return detect_lane_opponent_mia(
        active_position=active_position,
        enemies=enemies if enemies is not None else [_Enemy()],
        game_time=game_time,
        prev_last_cs_at=prev_last_cs_at or {},
        prev_cs=prev_cs or {},
        prev_alive=prev_alive or {},
        raw_events=raw_events,
    )


# ---------------------------------------------------------------------------
# find_lane_opponent
# ---------------------------------------------------------------------------

def test_find_returns_mid_lane_enemy() -> None:
    enemies = [_Enemy(position="TOP", champion_name="Garen"),
               _Enemy(position="MIDDLE", champion_name="Ahri")]
    found = find_lane_opponent("MIDDLE", enemies)
    assert found is not None
    assert getattr(found, "champion_name", "") == "Ahri"


def test_find_returns_none_when_active_is_jungle() -> None:
    enemies = [_Enemy(position="JUNGLE", champion_name="Jarvan")]
    assert find_lane_opponent("JUNGLE", enemies) is None


def test_find_returns_none_when_active_is_utility() -> None:
    """Supports excluded — CS signal too noisy for the SUP role."""
    enemies = [_Enemy(position="UTILITY", champion_name="Thresh")]
    assert find_lane_opponent("UTILITY", enemies) is None


def test_find_bot_lane_picks_adc_not_support() -> None:
    """Bot 2-vs-2: follow the ADC (BOTTOM), not the support (UTILITY)."""
    enemies = [
        _Enemy(position="UTILITY", champion_name="Thresh"),
        _Enemy(position="BOTTOM",  champion_name="Jinx"),
    ]
    found = find_lane_opponent("BOTTOM", enemies)
    assert getattr(found, "champion_name", "") == "Jinx"


def test_find_returns_none_when_no_match() -> None:
    enemies = [_Enemy(position="TOP")]
    assert find_lane_opponent("MIDDLE", enemies) is None


# ---------------------------------------------------------------------------
# detect_lane_opponent_mia — guards
# ---------------------------------------------------------------------------

def test_no_alert_for_jungle_position() -> None:
    alert, *_ = _run(active_position="JUNGLE")
    assert alert is None


def test_no_alert_for_utility_position() -> None:
    alert, *_ = _run(active_position="UTILITY")
    assert alert is None


def test_no_alert_before_lane_phase() -> None:
    alert, *_ = _run(game_time=LANE_PHASE_START_S - 1)
    assert alert is None


def test_no_alert_after_lane_phase() -> None:
    alert, *_ = _run(game_time=LANE_PHASE_END_S + 1)
    assert alert is None


def test_no_alert_when_opponent_dead() -> None:
    """Dead opponents don't warrant a 'where are they?' alert — we know."""
    enemies = [_Enemy(respawn_timer=20.0)]
    alert, *_ = _run(enemies=enemies)
    assert alert is None


def test_no_alert_when_no_lane_opponent() -> None:
    """Active player at MID, no MID enemy on the team."""
    enemies = [_Enemy(position="TOP")]
    alert, *_ = _run(enemies=enemies)
    assert alert is None


# ---------------------------------------------------------------------------
# detect_lane_opponent_mia — MIA thresholds
# ---------------------------------------------------------------------------

def test_first_sighting_anchors_clock_no_alert() -> None:
    """First call with no prior state shouldn't fire — we just anchor the clock."""
    alert, last_at, *_ = _run(game_time=300.0)
    assert alert is None
    # Check the anchor was recorded.
    assert last_at.get("Yasuo") == 300.0


def test_no_alert_when_recently_seen_csing() -> None:
    """20 s since last CS gain — below info threshold."""
    alert, *_ = _run(
        game_time=300.0,
        prev_last_cs_at={"Yasuo": 280.0},
        prev_cs={"Yasuo": 50},
        prev_alive={"Yasuo": True},
    )
    assert alert is None


def test_info_alert_at_info_threshold() -> None:
    """30 s no CS while alive → info."""
    alert, *_ = _run(
        game_time=400.0,
        enemies=[_Enemy(champion_name="Ahri", creep_score=80)],
        prev_last_cs_at={"Ahri": 400.0 - MIA_INFO_S},
        prev_cs={"Ahri": 80},  # CS hasn't moved
        prev_alive={"Ahri": True},
    )
    assert alert is not None
    assert alert.severity == "info"
    assert alert.opponent_name == "Ahri"


def test_warn_alert_at_warn_threshold() -> None:
    """60 s no CS while alive → warn."""
    alert, *_ = _run(
        game_time=400.0,
        enemies=[_Enemy(champion_name="Vex", creep_score=60)],
        prev_last_cs_at={"Vex": 400.0 - MIA_WARN_S},
        prev_cs={"Vex": 60},
        prev_alive={"Vex": True},
    )
    assert alert is not None
    assert alert.severity == "warn"


def test_cs_increase_resets_clock() -> None:
    """When CS goes up between ticks, last_cs_at refreshes to game_time."""
    alert, last_at, cs, _ = _run(
        game_time=400.0,
        enemies=[_Enemy(champion_name="Ahri", creep_score=82)],
        prev_last_cs_at={"Ahri": 350.0},
        prev_cs={"Ahri": 80},  # 80 → 82 = CSing
        prev_alive={"Ahri": True},
    )
    assert alert is None
    assert last_at["Ahri"] == 400.0
    assert cs["Ahri"] == 82


def test_respawn_resets_mia_clock() -> None:
    """Dead → alive transition: last_cs_at bumps to game_time."""
    alert, last_at, *_ = _run(
        game_time=500.0,
        enemies=[_Enemy(champion_name="Yone", creep_score=70, respawn_timer=0.0)],
        prev_last_cs_at={"Yone": 100.0},  # very stale before death
        prev_cs={"Yone": 70},
        prev_alive={"Yone": False},  # was dead last tick
    )
    assert alert is None
    assert last_at["Yone"] == 500.0


# ---------------------------------------------------------------------------
# detect_lane_opponent_mia — lane coverage
# ---------------------------------------------------------------------------

def test_fires_for_top_lane() -> None:
    enemies = [_Enemy(champion_name="Darius", position="TOP", creep_score=80)]
    alert, *_ = _run(
        active_position="TOP", enemies=enemies, game_time=400.0,
        prev_last_cs_at={"Darius": 360.0},
        prev_cs={"Darius": 80},
        prev_alive={"Darius": True},
    )
    assert alert is not None
    assert alert.active_position == "TOP"


def test_fires_for_bot_adc_only() -> None:
    """Active player BOT — alert tracks the BOTTOM enemy, not UTILITY."""
    enemies = [
        _Enemy(champion_name="Jinx", position="BOTTOM", creep_score=80),
        _Enemy(champion_name="Thresh", position="UTILITY", creep_score=10),
    ]
    alert, *_ = _run(
        active_position="BOTTOM", enemies=enemies, game_time=400.0,
        prev_last_cs_at={"Jinx": 360.0, "Thresh": 360.0},
        prev_cs={"Jinx": 80, "Thresh": 10},
        prev_alive={"Jinx": True, "Thresh": True},
    )
    assert alert is not None
    assert alert.opponent_name == "Jinx"


# ---------------------------------------------------------------------------
# rule_lane_opponent_mia — engine integration
# ---------------------------------------------------------------------------

@dataclass
class _Snap:
    game_time: float = 300.0
    lane_opponent_alert: object = None
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    raw_events: list = field(default_factory=list)
    active_team: str = ""
    active_summoner: str = ""
    active_level: int = 5
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    game_result: str = ""


from champ_assistant.advisor.decision_engine import (
    LANE_PHASE_EARLY_END_S,
    Recommendation,
    _suppress_dominated,
    rule_lane_opponent_mia,
)


def _alert(severity: str = "info", **overrides) -> LaneOpponentMia:
    return LaneOpponentMia(
        opponent_name=overrides.get("opponent_name", "Ahri"),
        seconds_mia=overrides.get("seconds_mia", 35.0),
        severity=severity,
        active_position=overrides.get("active_position", "MIDDLE"),
    )


def test_rule_silent_when_no_alert() -> None:
    assert rule_lane_opponent_mia(_Snap(lane_opponent_alert=None)) is None


def test_rule_info_fires() -> None:
    rec = rule_lane_opponent_mia(_Snap(
        game_time=300.0, lane_opponent_alert=_alert("info", seconds_mia=35.0),
    ))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "lane_mia"
    assert "Ahri" in rec.text


def test_rule_warn_fires() -> None:
    rec = rule_lane_opponent_mia(_Snap(
        game_time=400.0, lane_opponent_alert=_alert("warn", seconds_mia=65.0),
    ))
    assert rec is not None
    assert rec.severity == "warn"
    assert "65" in rec.text


def test_rule_advice_differs_early_vs_mid() -> None:
    """Early-lane advice should differ from mid-lane advice."""
    early = rule_lane_opponent_mia(_Snap(
        game_time=LANE_PHASE_EARLY_END_S - 60,
        lane_opponent_alert=_alert("warn", active_position="MIDDLE"),
    ))
    mid = rule_lane_opponent_mia(_Snap(
        game_time=LANE_PHASE_EARLY_END_S + 60,
        lane_opponent_alert=_alert("warn", active_position="MIDDLE"),
    ))
    assert early is not None and mid is not None
    assert early.text != mid.text


def test_rule_top_advice_mentions_herald_in_mid_phase() -> None:
    rec = rule_lane_opponent_mia(_Snap(
        game_time=LANE_PHASE_EARLY_END_S + 60,
        lane_opponent_alert=_alert("warn", active_position="TOP"),
    ))
    assert rec is not None
    assert "Herald" in rec.text


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="tempo",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_lane_mia_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("lane_mia", "warn")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "lane_mia" for r in result)


def test_lane_mia_suppressed_by_numbers_disadv() -> None:
    """Don't tell a short-handed player to push side-lanes alone."""
    recs = [_rec("numbers_disadv", "warn"), _rec("lane_mia", "info")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "lane_mia" for r in result)


def test_lane_mia_suppressed_by_ally_inhib_down() -> None:
    """When base is open, lane tempo is irrelevant — defend."""
    recs = [_rec("ally_inhib_down", "alert"), _rec("lane_mia", "warn")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "lane_mia" for r in result)


def test_lane_mia_suppressed_by_spiral_tilt() -> None:
    """Don't tell a feeding player to side-push alone."""
    recs = [_rec("tilt", "alert"), _rec("lane_mia", "info")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "lane_mia" for r in result)


def test_lane_mia_survives_normal_tilt_warn() -> None:
    """Non-spiral tilt (warn) shouldn't suppress lane tempo info."""
    recs = [_rec("tilt", "warn"), _rec("lane_mia", "info")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "lane_mia" for r in result)


# ---------------------------------------------------------------------------
# Combat-event reset (v1.10.132 — closes "I see the champ but the
# message still pops up" report)
# ---------------------------------------------------------------------------

def test_combat_event_resets_mia_clock() -> None:
    """If the lane opponent appears in a recent combat event (kill /
    multikill), they were visible to someone — reset the MIA clock so
    the rec doesn't fire."""
    enemy = _Enemy(champion_name="Yasuo", creep_score=50)
    # First tick within the laning window: anchor the clock at t=200
    # (must be ≥ LANE_PHASE_START_S=90 so the phase guard doesn't
    # short-circuit before any state is recorded).
    alert, last_seen, cs, alive = _run(
        enemies=[enemy], game_time=200.0,
    )
    assert alert is None
    # 35 s later, no CS gained — but Yasuo just got a kill mid-game.
    # A normal MIA detection would fire (info threshold = 30 s).
    # Combat-event reset should clear the clock.
    enemy_no_cs = _Enemy(champion_name="Yasuo", creep_score=50)
    alert, *_ = _run(
        enemies=[enemy_no_cs],
        game_time=240.0,
        prev_last_cs_at=last_seen,
        prev_cs=cs,
        prev_alive=alive,
        raw_events=[
            {"EventName": "ChampionKill",
             "KillerName": "Yasuo", "VictimName": "OtherPlayer",
             "EventTime": 235.0},
        ],
    )
    assert alert is None, (
        "lane opponent appeared in a combat event — MIA clock should "
        "be reset, no rec should fire"
    )


def test_combat_event_as_victim_also_resets_clock() -> None:
    """Symmetry — being killed counts as visible too."""
    enemy = _Enemy(champion_name="Yasuo", creep_score=50)
    _, last_seen, cs, alive = _run(enemies=[enemy], game_time=200.0)
    alert, *_ = _run(
        enemies=[enemy],
        game_time=240.0,
        prev_last_cs_at=last_seen,
        prev_cs=cs,
        prev_alive=alive,
        raw_events=[
            {"EventName": "ChampionKill",
             "KillerName": "OtherPlayer", "VictimName": "Yasuo",
             "EventTime": 235.0},
        ],
    )
    assert alert is None


def test_unrelated_combat_event_does_not_reset_clock() -> None:
    """A kill involving someone OTHER than the lane opponent must NOT
    reset their clock — we still want the MIA rec to fire."""
    enemy = _Enemy(champion_name="Yasuo", creep_score=50)
    _, last_seen, cs, alive = _run(enemies=[enemy], game_time=200.0)
    alert, *_ = _run(
        enemies=[enemy],
        game_time=240.0,
        prev_last_cs_at=last_seen,
        prev_cs=cs,
        prev_alive=alive,
        raw_events=[
            {"EventName": "ChampionKill",
             "KillerName": "Garen", "VictimName": "Caitlyn",
             "EventTime": 235.0},
        ],
    )
    # Yasuo wasn't in the kill — info-tier MIA fires (240 s ≥ 30 s).
    assert alert is not None
    assert alert.opponent_name == "Yasuo"
