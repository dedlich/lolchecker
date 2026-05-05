"""Tests for gank window detection (Charter B2)."""
from __future__ import annotations

from dataclasses import dataclass, field

from champ_assistant.lcda.gank_window import (
    GANK_PHASE_END_S,
    GANK_PHASE_START_S,
    MIA_INFO_S,
    MIA_WARN_S,
    GankAlert,
    detect_gank_risk,
    find_enemy_jungler,
    last_combat_time,
)


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Player:
    position: str = "MIDDLE"
    champion_name: str = "LeeSin"
    summoner_name: str = "Jungler1"
    respawn_timer: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self.respawn_timer <= 0.0


def _jg(**kwargs) -> _Player:
    return _Player(position="JUNGLE", **kwargs)


def _kill(killer: str = "", victim: str = "", assisters: list | None = None,
          t: float = 100.0) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": assisters or [],
    }


def _run(
    *,
    active_position: str = "TOP",
    jungler: _Player | None = None,
    events: list | None = None,
    game_time: float = 400.0,
    prev_last_seen_gt: float = 0.0,
    prev_was_alive: bool = False,
) -> tuple:
    enemies = [jungler] if jungler is not None else []
    return detect_gank_risk(
        active_position=active_position,
        enemies=enemies,
        events=events or [],
        game_time=game_time,
        prev_last_seen_gt=prev_last_seen_gt,
        prev_was_alive=prev_was_alive,
    )


# ---------------------------------------------------------------------------
# find_enemy_jungler
# ---------------------------------------------------------------------------

def test_find_jungler_returns_jungle_player() -> None:
    players = [_Player(position="TOP"), _jg(champion_name="Jarvan")]
    found = find_enemy_jungler(players)
    assert found is not None
    assert getattr(found, "champion_name", "") == "Jarvan"


def test_find_jungler_returns_none_when_absent() -> None:
    players = [_Player(position="TOP"), _Player(position="MIDDLE")]
    assert find_enemy_jungler(players) is None


def test_find_jungler_returns_none_on_empty_list() -> None:
    assert find_enemy_jungler([]) is None


# ---------------------------------------------------------------------------
# last_combat_time
# ---------------------------------------------------------------------------

def test_last_combat_time_killer_match() -> None:
    events = [_kill(killer="LeeSin", t=150.0)]
    assert last_combat_time({"LeeSin"}, events) == 150.0


def test_last_combat_time_victim_match() -> None:
    events = [_kill(victim="LeeSin", t=200.0)]
    assert last_combat_time({"LeeSin"}, events) == 200.0


def test_last_combat_time_assister_match() -> None:
    events = [_kill(assisters=["LeeSin", "Other"], t=175.0)]
    assert last_combat_time({"LeeSin"}, events) == 175.0


def test_last_combat_time_returns_max_event() -> None:
    events = [
        _kill(killer="LeeSin", t=100.0),
        _kill(victim="LeeSin", t=250.0),
        _kill(assisters=["LeeSin"], t=180.0),
    ]
    assert last_combat_time({"LeeSin"}, events) == 250.0


def test_last_combat_time_zero_when_no_match() -> None:
    events = [_kill(killer="Kayn", t=100.0)]
    assert last_combat_time({"LeeSin"}, events) == 0.0


def test_last_combat_time_ignores_non_champion_kill_events() -> None:
    events = [
        {"EventName": "DragonKill", "EventTime": 500.0, "KillerName": "LeeSin"},
    ]
    assert last_combat_time({"LeeSin"}, events) == 0.0


def test_last_combat_time_matches_summoner_name_too() -> None:
    events = [_kill(killer="SummonerFoo", t=120.0)]
    # ids contains both summoner name AND champion name
    assert last_combat_time({"SummonerFoo", "LeeSin"}, events) == 120.0


# ---------------------------------------------------------------------------
# detect_gank_risk — position / phase guards
# ---------------------------------------------------------------------------

def test_no_alert_for_jungle_position() -> None:
    alert, _, _ = _run(active_position="JUNGLE", jungler=_jg())
    assert alert is None


def test_no_alert_for_utility_position() -> None:
    alert, _, _ = _run(active_position="UTILITY", jungler=_jg())
    assert alert is None


def test_no_alert_before_gank_phase() -> None:
    alert, _, _ = _run(
        active_position="TOP",
        jungler=_jg(),
        game_time=GANK_PHASE_START_S - 1,
    )
    assert alert is None


def test_no_alert_after_laning_phase() -> None:
    alert, _, _ = _run(
        active_position="TOP",
        jungler=_jg(),
        game_time=GANK_PHASE_END_S + 1,
    )
    assert alert is None


def test_no_alert_when_no_enemy_jungler() -> None:
    alert, _, _ = _run(active_position="TOP", jungler=None)
    assert alert is None


def test_no_alert_when_jungler_is_dead() -> None:
    dead_jungler = _jg(respawn_timer=20.0)
    alert, _, _ = _run(active_position="TOP", jungler=dead_jungler)
    assert alert is None


# ---------------------------------------------------------------------------
# detect_gank_risk — MIA thresholds
# ---------------------------------------------------------------------------

def test_no_alert_when_recently_seen() -> None:
    """Jungler appeared in a kill event 30 s ago — below info threshold."""
    game_time = 500.0
    last_seen = game_time - 30.0
    alert, _, _ = _run(
        active_position="TOP",
        jungler=_jg(),
        game_time=game_time,
        prev_last_seen_gt=last_seen,
        prev_was_alive=True,
    )
    assert alert is None


def test_info_alert_at_info_threshold() -> None:
    game_time = 500.0
    last_seen = game_time - MIA_INFO_S
    alert, _, _ = _run(
        active_position="MIDDLE",
        jungler=_jg(champion_name="Vi"),
        game_time=game_time,
        prev_last_seen_gt=last_seen,
        prev_was_alive=True,
    )
    assert alert is not None
    assert alert.severity == "info"
    assert alert.jungler_name == "Vi"
    assert alert.seconds_mia >= MIA_INFO_S


def test_warn_alert_at_warn_threshold() -> None:
    game_time = 600.0
    last_seen = game_time - MIA_WARN_S
    alert, _, _ = _run(
        active_position="BOTTOM",
        jungler=_jg(champion_name="Elise"),
        game_time=game_time,
        prev_last_seen_gt=last_seen,
        prev_was_alive=True,
    )
    assert alert is not None
    assert alert.severity == "warn"
    assert alert.jungler_name == "Elise"


def test_mia_updates_from_event_log() -> None:
    """A kill event 20 s ago resets the MIA clock below the threshold."""
    game_time = 500.0
    events = [_kill(killer="Jungler1", t=game_time - 20.0)]
    alert, new_last_seen, _ = _run(
        active_position="TOP",
        jungler=_jg(summoner_name="Jungler1"),
        events=events,
        game_time=game_time,
        prev_last_seen_gt=0.0,
        prev_was_alive=True,
    )
    assert alert is None
    assert new_last_seen == pytest.approx(game_time - 20.0)


def test_respawn_resets_mia_clock() -> None:
    """Dead → alive transition: last_seen_gt bumps to game_time."""
    game_time = 500.0
    # Previously dead (prev_was_alive=False), now alive.
    alert, new_last_seen, new_alive = _run(
        active_position="TOP",
        jungler=_jg(respawn_timer=0.0),  # now alive
        game_time=game_time,
        prev_last_seen_gt=50.0,          # very stale last-seen before death
        prev_was_alive=False,            # was dead last tick
    )
    # Just respawned — MIA seconds = 0 → no alert.
    assert alert is None
    assert new_last_seen == game_time
    assert new_alive is True


def test_state_carries_forward_when_no_events() -> None:
    """prev_last_seen_gt persists when no new events involve the jungler."""
    game_time = 500.0
    prev = 450.0  # 50 s ago — below MIA_INFO_S threshold
    alert, new_last_seen, _ = _run(
        active_position="TOP",
        jungler=_jg(),
        events=[],
        game_time=game_time,
        prev_last_seen_gt=prev,
        prev_was_alive=True,
    )
    assert alert is None
    assert new_last_seen == prev  # unchanged


# ---------------------------------------------------------------------------
# detect_gank_risk — all three lane roles work
# ---------------------------------------------------------------------------

def test_fires_for_top_lane() -> None:
    alert, _, _ = _run(active_position="TOP", jungler=_jg(), prev_was_alive=True)
    # At game_time=400 with last_seen=0 baseline=GANK_PHASE_START_S=240
    # mia = 400-240 = 160s → warn
    assert alert is not None


def test_fires_for_mid_lane() -> None:
    alert, _, _ = _run(active_position="MIDDLE", jungler=_jg(), prev_was_alive=True)
    assert alert is not None


def test_fires_for_bot_lane() -> None:
    alert, _, _ = _run(active_position="BOTTOM", jungler=_jg(), prev_was_alive=True)
    assert alert is not None


# ---------------------------------------------------------------------------
# rule_gank_risk integration (decision engine)
# ---------------------------------------------------------------------------

import pytest
from dataclasses import dataclass as _dc, field as _field


@_dc
class _GankSnap:
    game_time: float = 500.0
    gank_alert: object = None
    enemies: list = _field(default_factory=list)
    allies: list = _field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = _field(default_factory=list)
    raw_events: list = _field(default_factory=list)
    active_team: str = ""
    active_summoner: str = ""
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = _field(default_factory=list)
    enemy_spikes: list = _field(default_factory=list)
    game_result: str = ""


from champ_assistant.advisor.decision_engine import rule_gank_risk, _suppress_dominated
from champ_assistant.advisor.decision_engine import Recommendation


def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(text="x", severity=severity, category="safety",
                          confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind)


def test_rule_gank_risk_warn_fires() -> None:
    alert = GankAlert(jungler_name="LeeSin", seconds_mia=95.0, severity="warn")
    snap = _GankSnap(gank_alert=alert)
    rec = rule_gank_risk(snap)
    assert rec is not None
    assert rec.severity == "warn"
    assert rec.kind == "gank_risk"
    assert "LeeSin" in rec.text
    assert "95" in rec.text


def test_rule_gank_risk_info_fires() -> None:
    alert = GankAlert(jungler_name="Vi", seconds_mia=65.0, severity="info")
    snap = _GankSnap(gank_alert=alert)
    rec = rule_gank_risk(snap)
    assert rec is not None
    assert rec.severity == "info"
    assert "Vi" in rec.text


def test_rule_gank_risk_silent_when_no_alert() -> None:
    snap = _GankSnap(gank_alert=None)
    assert rule_gank_risk(snap) is None


def test_gank_risk_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("gank_risk", "warn")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "gank_risk" for r in result)


def test_gank_risk_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("gank_risk", "info")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "gank_risk" for r in result)


def test_gank_risk_survives_numbers_disadv() -> None:
    """Jungler MIA info is still relevant even when a teammate is dead."""
    recs = [_rec("numbers_disadv", "warn"), _rec("gank_risk", "warn")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "gank_risk" for r in result)
