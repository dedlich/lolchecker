"""Tests for personal tilt / death-pattern detection (Charter B4)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.lcda.tilt import (
    BOUNTY_KILL_STREAK,
    LANE_PHASE_END_S,
    SOLO_DEATH_TEAMFIGHT_WINDOW_S,
    SPIRAL_RECENT_180_COUNT,
    TILT_DEATH_COUNT,
    TiltState,
    detect_tilt,
)


# ---------------------------------------------------------------------------
# Stub helpers — minimal event factories
# ---------------------------------------------------------------------------

ME = "Me"
ME_CHAMP = "Yasuo"
ALLY1 = "Ally1"
ALLY1_CHAMP = "Lulu"
ENEMY1 = "Enemy1"


def _kill(killer: str = "", victim: str = "", t: float = 100.0,
          assisters: list | None = None) -> dict:
    return {
        "EventName": "ChampionKill",
        "EventTime": t,
        "KillerName": killer,
        "VictimName": victim,
        "Assisters": assisters or [],
    }


def _run(events: list, *, game_time: float = 600.0,
         active_ids: set[str] | None = None,
         ally_ids: set[str] | None = None) -> TiltState | None:
    return detect_tilt(
        active_ids=active_ids if active_ids is not None else {ME, ME_CHAMP},
        ally_ids=ally_ids if ally_ids is not None else {ALLY1, ALLY1_CHAMP},
        events=events,
        game_time=game_time,
    )


# ---------------------------------------------------------------------------
# Empty / no-data cases
# ---------------------------------------------------------------------------

def test_no_data_returns_none() -> None:
    assert _run([]) is None


def test_no_active_deaths_returns_none() -> None:
    """Other people died but I didn't — no tilt to report."""
    events = [_kill(killer=ME, victim=ENEMY1, t=100.0)]
    assert _run(events) is None


def test_empty_active_ids_returns_none() -> None:
    """Defensive: no active player identity yet."""
    assert _run([_kill(victim=ME, t=100.0)], active_ids=set()) is None


# ---------------------------------------------------------------------------
# Tier ladder
# ---------------------------------------------------------------------------

def test_caution_tier_for_single_lane_death() -> None:
    """One death in lane phase fires caution."""
    state = _run([_kill(victim=ME, t=300.0)], game_time=350.0)
    assert state is not None
    assert state.severity == "caution"
    assert state.deaths_total == 1


def test_no_caution_after_lane_phase() -> None:
    """One death past 14:00 doesn't fire caution — lane fix doesn't apply."""
    state = _run(
        [_kill(victim=ME, t=LANE_PHASE_END_S + 60.0)],
        game_time=LANE_PHASE_END_S + 90.0,
    )
    assert state is not None
    assert state.severity == "ok"


def test_tilt_tier_for_two_deaths_in_90s() -> None:
    """Classic tilt window: 2 deaths in 90s but spread (>60s apart)."""
    events = [
        _kill(victim=ME, t=500.0),
        _kill(victim=ME, t=580.0),  # 80s after first
    ]
    state = _run(events, game_time=590.0)
    assert state is not None
    assert state.severity == "tilt"
    assert state.deaths_recent_90s >= TILT_DEATH_COUNT


def test_re_engage_tier_for_two_deaths_in_60s() -> None:
    """1-and-done pattern: died, respawned, died again immediately."""
    events = [
        _kill(victim=ME, t=500.0),
        _kill(victim=ME, t=540.0),  # 40s — within 60s window
    ]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.severity == "re_engage"


def test_spiral_tier_for_three_deaths_in_180s() -> None:
    """3 deaths in 3 minutes spread out enough to avoid the 60s tier."""
    events = [
        _kill(victim=ME, t=500.0),
        _kill(victim=ME, t=580.0),
        _kill(victim=ME, t=660.0),  # 160s span, all within 180s of game_time=670
    ]
    state = _run(events, game_time=670.0)
    assert state is not None
    assert state.severity == "spiral"
    assert state.deaths_recent_180s >= SPIRAL_RECENT_180_COUNT


def test_old_deaths_outside_window_dont_count() -> None:
    """A death from 4 minutes ago is past every recent window."""
    events = [_kill(victim=ME, t=100.0)]
    state = _run(events, game_time=400.0)
    assert state is not None
    assert state.deaths_recent_60s == 0
    assert state.deaths_recent_90s == 0
    assert state.deaths_recent_180s == 0
    assert state.severity == "ok"


def test_severity_uses_most_severe_tier() -> None:
    """Test that 4 deaths in 60s definitely picks spiral over re_engage."""
    events = [_kill(victim=ME, t=540.0 + i) for i in range(4)]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.severity == "spiral"


# ---------------------------------------------------------------------------
# Bounty modifier
# ---------------------------------------------------------------------------

def test_bounty_lost_when_died_on_streak() -> None:
    """3 unanswered kills before a death = bounty lost."""
    events = [
        _kill(killer=ME, victim=ENEMY1, t=200.0),
        _kill(killer=ME, victim=ENEMY1, t=300.0),
        _kill(killer=ME, victim=ENEMY1, t=400.0),
        _kill(victim=ME, t=500.0),
    ]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.bounty_lost is True


def test_no_bounty_when_below_streak_threshold() -> None:
    """2 kills < BOUNTY_KILL_STREAK (3) → no bounty."""
    events = [
        _kill(killer=ME, victim=ENEMY1, t=200.0),
        _kill(killer=ME, victim=ENEMY1, t=300.0),
        _kill(victim=ME, t=500.0),
    ]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.bounty_lost is False


def test_bounty_resets_after_prior_death() -> None:
    """Streak counts only kills since the *previous* death — earlier kills
    on a prior life don't carry over."""
    events = [
        _kill(killer=ME, victim=ENEMY1, t=100.0),
        _kill(killer=ME, victim=ENEMY1, t=150.0),
        _kill(victim=ME, t=200.0),     # earlier death — streak reset
        _kill(killer=ME, victim=ENEMY1, t=300.0),
        _kill(victim=ME, t=400.0),     # only 1 kill since previous death
    ]
    state = _run(events, game_time=450.0)
    assert state is not None
    assert state.bounty_lost is False


# ---------------------------------------------------------------------------
# Solo-death modifier
# ---------------------------------------------------------------------------

def test_solo_death_when_no_ally_involvement() -> None:
    """Died with no ally as assister and no ally death within ±5s."""
    events = [_kill(killer=ENEMY1, victim=ME, t=500.0, assisters=[])]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.solo_death is True


def test_not_solo_when_ally_assisted() -> None:
    """If an ally fought and got an assist credit, it wasn't a solo death."""
    events = [_kill(killer=ENEMY1, victim=ME, t=500.0, assisters=[ALLY1])]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.solo_death is False


def test_not_solo_when_ally_died_in_teamfight_window() -> None:
    """Ally died within ±5s of me → it was a teamfight, not solo."""
    events = [
        _kill(killer=ENEMY1, victim=ALLY1, t=502.0),  # ally dies 2s after me
        _kill(killer=ENEMY1, victim=ME, t=500.0),
    ]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.solo_death is False


def test_solo_when_ally_death_far_from_window() -> None:
    """Ally died 10s after me — outside the 5s teamfight window."""
    events = [
        _kill(killer=ENEMY1, victim=ME, t=500.0),
        _kill(killer=ENEMY1, victim=ALLY1, t=515.0),  # 15s gap
    ]
    state = _run(events, game_time=550.0)
    assert state is not None
    assert state.solo_death is True


# ---------------------------------------------------------------------------
# rule_tilt_detection (engine integration)
# ---------------------------------------------------------------------------

@dataclass
class _Snap:
    game_time: float = 600.0
    tilt_state: TiltState | None = None
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    raw_events: list = field(default_factory=list)
    active_team: str = ""
    active_summoner: str = ""
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    game_result: str = ""


from champ_assistant.advisor.decision_engine import (
    Recommendation,
    _suppress_dominated,
    rule_tilt_detection,
)


def _state(severity: str, **overrides) -> TiltState:
    base = dict(
        severity=severity,
        deaths_total=overrides.get("deaths_total", 2),
        deaths_recent_60s=overrides.get("deaths_recent_60s", 1),
        deaths_recent_90s=overrides.get("deaths_recent_90s", 2),
        deaths_recent_180s=overrides.get("deaths_recent_180s", 2),
        last_death_at=overrides.get("last_death_at", 500.0),
        bounty_lost=overrides.get("bounty_lost", False),
        solo_death=overrides.get("solo_death", False),
    )
    return TiltState(**base)


def test_rule_silent_when_no_state() -> None:
    assert rule_tilt_detection(_Snap(tilt_state=None)) is None


def test_rule_silent_when_severity_ok() -> None:
    assert rule_tilt_detection(_Snap(tilt_state=_state("ok"))) is None


def test_rule_caution_fires_info() -> None:
    rec = rule_tilt_detection(_Snap(
        game_time=400.0,
        tilt_state=_state("caution", deaths_total=1, deaths_recent_90s=1),
    ))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "tilt"
    assert "Erster Tod" in rec.text


def test_rule_tilt_fires_warn() -> None:
    rec = rule_tilt_detection(_Snap(
        game_time=600.0, tilt_state=_state("tilt"),
    ))
    assert rec is not None
    assert rec.severity == "warn"
    assert "Tilt" in rec.text or "tilt" in rec.text.lower()


def test_rule_re_engage_fires_alert() -> None:
    rec = rule_tilt_detection(_Snap(tilt_state=_state("re_engage")))
    assert rec is not None
    assert rec.severity == "alert"
    assert "1-AND-DONE" in rec.text


def test_rule_spiral_fires_alert() -> None:
    rec = rule_tilt_detection(_Snap(
        tilt_state=_state("spiral", deaths_recent_180s=3),
    ))
    assert rec is not None
    assert rec.severity == "alert"
    assert "DEATH SPIRAL" in rec.text


def test_rule_includes_bounty_modifier() -> None:
    rec = rule_tilt_detection(_Snap(
        tilt_state=_state("tilt", bounty_lost=True),
    ))
    assert rec is not None
    assert "Bounty" in rec.text


def test_rule_includes_solo_modifier() -> None:
    rec = rule_tilt_detection(_Snap(
        tilt_state=_state("tilt", solo_death=True),
    ))
    assert rec is not None
    assert "Alleine" in rec.text


def test_rule_phase_awareness_lane_vs_late() -> None:
    """Lane-phase advice differs from late-game advice."""
    lane_rec = rule_tilt_detection(_Snap(
        game_time=400.0, tilt_state=_state("tilt"),
    ))
    late_rec = rule_tilt_detection(_Snap(
        game_time=1700.0, tilt_state=_state("tilt"),
    ))
    assert lane_rec is not None and late_rec is not None
    assert lane_rec.text != late_rec.text


# ---------------------------------------------------------------------------
# Suppression rules
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="safety",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_tilt_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("tilt", "warn")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "tilt" for r in result)


def test_tilt_survives_numbers_disadv() -> None:
    """Personal tilt is more specific than team-level numbers_disadv."""
    recs = [_rec("numbers_disadv", "warn"), _rec("tilt", "warn")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "tilt" for r in result)


def test_tilt_survives_ally_inhib_down() -> None:
    """Defending without dying again is critical when base is open."""
    recs = [_rec("ally_inhib_down", "alert"), _rec("tilt", "alert")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "tilt" for r in result)


def test_spiral_tilt_suppresses_offensive_calls() -> None:
    """Spiral tilt (alert severity) drops fight/power_spike/etc."""
    recs = [
        _rec("tilt", "alert"),
        _rec("fight", "warn"),
        _rec("power_spike", "alert"),
        _rec("gold_lead", "info"),
    ]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "tilt" in kinds
    assert "fight" not in kinds
    assert "power_spike" not in kinds
    assert "gold_lead" not in kinds


def test_non_alert_tilt_does_not_suppress_offensive_calls() -> None:
    """Caution / tilt (warn) leave fight calls intact — only spiral suppresses."""
    recs = [_rec("tilt", "warn"), _rec("fight", "warn")]
    result = _suppress_dominated(recs)
    kinds = {r.kind for r in result}
    assert "fight" in kinds
