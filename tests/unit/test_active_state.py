"""Tests for active-player combat state extraction + recall coaching."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.lcda.active_state import (
    ActiveCombatState,
    extract_active_combat_state,
)


@pytest.fixture(autouse=True)
def _reset_recall_hysteresis():
    """The recall rule maintains a process-wide armed/disarmed state per
    tier so it doesn't re-fire every 2 s tick. Tests that call the rule
    repeatedly with the same trigger conditions must each start armed —
    otherwise the second test sees a disarmed flag and returns None
    unexpectedly."""
    from champ_assistant.advisor.decision_engine import reset_recall_hysteresis
    reset_recall_hysteresis()
    yield
    reset_recall_hysteresis()


# ---------------------------------------------------------------------------
# extract_active_combat_state
# ---------------------------------------------------------------------------

def test_extract_returns_default_when_none() -> None:
    state = extract_active_combat_state(None)
    assert state.gold == 0.0
    assert state.hp_pct == 1.0
    assert state.mana_pct == 1.0
    assert state.is_mana_user is False


def test_extract_returns_default_when_empty() -> None:
    state = extract_active_combat_state({})
    assert state.hp_pct == 1.0
    assert state.mana_pct == 1.0


def test_extract_pulls_gold() -> None:
    state = extract_active_combat_state({"currentGold": 1452.5})
    assert state.gold == 1452.5


def test_extract_computes_hp_pct() -> None:
    state = extract_active_combat_state({
        "championStats": {"currentHealth": 600, "maxHealth": 1200},
    })
    assert state.hp_pct == 0.5


def test_extract_clamps_hp_pct_above_one() -> None:
    """Bug-state where current > max (heal buff drop) shouldn't push above 1."""
    state = extract_active_combat_state({
        "championStats": {"currentHealth": 1500, "maxHealth": 1000},
    })
    assert state.hp_pct == 1.0


def test_extract_clamps_hp_pct_below_zero() -> None:
    state = extract_active_combat_state({
        "championStats": {"currentHealth": -50, "maxHealth": 1000},
    })
    assert state.hp_pct == 0.0


def test_extract_handles_zero_max_hp() -> None:
    """Defensive — div-by-zero protection."""
    state = extract_active_combat_state({
        "championStats": {"currentHealth": 0, "maxHealth": 0},
    })
    assert state.hp_pct == 1.0  # treated as "unknown" → full


def test_extract_marks_mana_user() -> None:
    state = extract_active_combat_state({
        "championStats": {
            "currentHealth": 100, "maxHealth": 100,
            "resourceValue": 50, "resourceMax": 100,
            "resourceType": "MANA",
        },
    })
    assert state.is_mana_user is True
    assert state.mana_pct == 0.5


def test_extract_treats_energy_as_non_mana() -> None:
    """Energy users (Akali / Zed / Lee Sin) regen too fast for mana coaching."""
    state = extract_active_combat_state({
        "championStats": {
            "currentHealth": 100, "maxHealth": 100,
            "resourceValue": 50, "resourceMax": 200,
            "resourceType": "ENERGY",
        },
    })
    assert state.is_mana_user is False
    # Non-mana users get mana_pct=1.0 so the rule short-circuits.
    assert state.mana_pct == 1.0


def test_extract_handles_string_resource_type_case() -> None:
    """LCDA returns 'mana' or 'MANA' depending on patch — case-insensitive match."""
    state = extract_active_combat_state({
        "championStats": {"resourceValue": 50, "resourceMax": 100, "resourceType": "mana"},
    })
    assert state.is_mana_user is True


def test_extract_robust_to_non_dict_championstats() -> None:
    """Defensive — championStats may be missing or malformed."""
    state = extract_active_combat_state({
        "currentGold": 500, "championStats": "garbage",
    })
    assert state.gold == 500
    assert state.hp_pct == 1.0


# ---------------------------------------------------------------------------
# rule_recall_check — engine integration
# ---------------------------------------------------------------------------

@dataclass
class _Snap:
    game_time: float = 600.0
    active_combat: ActiveCombatState = field(default_factory=ActiveCombatState)
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
    tilt_state: object = None
    game_result: str = ""


from champ_assistant.advisor.decision_engine import (
    GOLD_BACK_WORTH,
    GOLD_COMPONENT_SPIKE,
    HP_CRITICAL_PCT,
    HP_LOW_PCT,
    MANA_DEPLETED_PCT,
    MANA_LOW_PCT,
    RECALL_PHASE_END_S,
    Recommendation,
    _suppress_dominated,
    rule_recall_check,
)


def _state(**overrides) -> ActiveCombatState:
    base = dict(
        gold=overrides.get("gold", 500.0),
        hp_pct=overrides.get("hp_pct", 1.0),
        mana_pct=overrides.get("mana_pct", 1.0),
        is_mana_user=overrides.get("is_mana_user", True),
        resource_type=overrides.get("resource_type", "MANA"),
        unspent_skill_points=overrides.get("unspent_skill_points", 0),
    )
    return ActiveCombatState(**base)


# Tier 1: critical HP

def test_critical_hp_fires_alert() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(hp_pct=0.20)))
    assert rec is not None
    assert rec.severity == "alert"
    assert rec.kind == "recall_critical"
    assert "20%" in rec.text


def test_critical_hp_overrides_phase() -> None:
    """Critical HP fires even past the recall-phase cutoff."""
    rec = rule_recall_check(_Snap(
        game_time=RECALL_PHASE_END_S + 600.0,
        active_combat=_state(hp_pct=0.15),
    ))
    assert rec is not None
    assert rec.severity == "alert"


def test_dead_player_gets_no_advice() -> None:
    """hp_pct == 0 means dead — no alert needed (they can't act)."""
    rec = rule_recall_check(_Snap(active_combat=_state(hp_pct=0.0, gold=2000.0)))
    assert rec is None


# Tier 2: resource depleted + back-worth gold

def test_resource_depleted_with_gold_fires_warn() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.45, gold=GOLD_BACK_WORTH + 100,
    )))
    assert rec is not None
    assert rec.severity == "warn"
    assert rec.kind == "recall_resource"


def test_low_mana_with_gold_fires_warn() -> None:
    """Mana < 30% triggers tier 2 even at full HP."""
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.95, mana_pct=0.20, gold=GOLD_BACK_WORTH + 200,
    )))
    assert rec is not None
    assert rec.kind == "recall_resource"
    assert "Mana" in rec.text


def test_resource_low_without_gold_does_not_fire_tier2() -> None:
    """Half HP but only 500g — tier 2 needs ≥ 1100g to be worth backing."""
    rec = rule_recall_check(_Snap(active_combat=_state(hp_pct=0.45, gold=500.0)))
    # Not None necessarily — mana_pct=1.0 by default and gold < tier 3.
    # So no rec.
    assert rec is None


def test_resource_tier_silent_past_phase_cutoff() -> None:
    rec = rule_recall_check(_Snap(
        game_time=RECALL_PHASE_END_S + 60,
        active_combat=_state(hp_pct=0.45, gold=1200.0),
    ))
    assert rec is None  # only tier 1 fires past phase cutoff


# Tier 3: pure gold opportunity

def test_pure_gold_opportunity_fires_info() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.85, gold=GOLD_COMPONENT_SPIKE + 50,
    )))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "recall_gold"


def test_below_component_spike_does_not_fire_tier3() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.95, gold=GOLD_COMPONENT_SPIKE - 100,
    )))
    assert rec is None


# Tier 4: mana check

def test_low_mana_in_lane_fires_info() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.85, mana_pct=0.10, gold=300.0, is_mana_user=True,
    )))
    assert rec is not None
    assert rec.kind == "mana_check"


def test_mana_check_skipped_for_energy_users() -> None:
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.85, mana_pct=0.05, gold=300.0, is_mana_user=False,
    )))
    assert rec is None  # energy regenerates fast, no warning


# Priority — multiple tiers possible at once
def test_critical_hp_beats_all_other_tiers() -> None:
    """Critical HP + 2000g + low mana → still picks critical alert."""
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.15, mana_pct=0.05, gold=2000.0,
    )))
    assert rec is not None
    assert rec.kind == "recall_critical"


def test_resource_tier_beats_pure_gold() -> None:
    """Half HP + 1500g should pick the warn tier over the info tier."""
    rec = rule_recall_check(_Snap(active_combat=_state(
        hp_pct=0.45, gold=1500.0,
    )))
    assert rec is not None
    assert rec.severity == "warn"


# Suppression
def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="safety",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_recall_resource_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("recall_resource", "warn")]
    result = _suppress_dominated(recs)
    assert not any(r.kind == "recall_resource" for r in result)


def test_recall_critical_survives_ace() -> None:
    """Critical HP overrides ace push — you can't help by feeding."""
    recs = [_rec("ace", "alert"), _rec("recall_critical", "alert")]
    result = _suppress_dominated(recs)
    assert any(r.kind == "recall_critical" for r in result)


def test_recall_check_silent_when_no_state() -> None:
    snap = _Snap(active_combat=None)  # type: ignore
    assert rule_recall_check(snap) is None


# ---------------------------------------------------------------------------
# Skill-point extraction
# ---------------------------------------------------------------------------

def test_extract_unspent_skill_points_zero_when_fully_spent() -> None:
    state = extract_active_combat_state({
        "level": 5,
        "abilities": {
            "Q": {"abilityLevel": 3},
            "W": {"abilityLevel": 1},
            "E": {"abilityLevel": 1},
            "R": {"abilityLevel": 0},
        },
    })
    assert state.unspent_skill_points == 0


def test_extract_unspent_skill_points_one_when_one_unspent() -> None:
    state = extract_active_combat_state({
        "level": 6,
        "abilities": {
            "Q": {"abilityLevel": 3},
            "W": {"abilityLevel": 1},
            "E": {"abilityLevel": 1},
            "R": {"abilityLevel": 0},  # didn't take ult yet
        },
    })
    assert state.unspent_skill_points == 1


def test_extract_unspent_skill_points_handles_legacy_level_field() -> None:
    """Older LCDA patches used 'level' instead of 'abilityLevel'."""
    state = extract_active_combat_state({
        "level": 4,
        "abilities": {
            "Q": {"level": 2},
            "W": {"level": 1},
            "E": {"level": 0},
            "R": {"level": 0},
        },
    })
    assert state.unspent_skill_points == 1


def test_extract_unspent_handles_missing_abilities() -> None:
    """Defensive — abilities dict may be absent on older clients."""
    state = extract_active_combat_state({"level": 5})
    # No abilities → no spent points known → all 5 reported as unspent
    # (better to over-fire than under-fire on missing data so the user
    # notices the patch incompatibility).
    assert state.unspent_skill_points == 5


def test_extract_unspent_clamps_at_zero() -> None:
    """Bug-state where ability levels exceed player level shouldn't go negative."""
    state = extract_active_combat_state({
        "level": 1,
        "abilities": {
            "Q": {"abilityLevel": 5},  # impossible but defensive
        },
    })
    assert state.unspent_skill_points == 0


# ---------------------------------------------------------------------------
# rule_unspent_skill_points
# ---------------------------------------------------------------------------

from champ_assistant.advisor.decision_engine import rule_unspent_skill_points


def test_skill_point_rule_fires_when_unspent_and_safe() -> None:
    rec = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=0.85, unspent_skill_points=1),
    ))
    assert rec is not None
    assert rec.kind == "skill_point_unspent"
    assert rec.severity == "info"
    assert "Skill" in rec.text


def test_skill_point_rule_silent_when_zero() -> None:
    rec = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=1.0, unspent_skill_points=0),
    ))
    assert rec is None


def test_skill_point_rule_silent_below_hp_gate() -> None:
    """Don't nag during a trade — player needs to focus on combat."""
    rec = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=0.30, unspent_skill_points=1),
    ))
    assert rec is None


def test_skill_point_rule_silent_when_dead() -> None:
    rec = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=0.0, unspent_skill_points=1),
    ))
    assert rec is None


def test_skill_point_rule_silent_in_first_minute() -> None:
    """Game-start grace — first wave hasn't crashed yet."""
    rec = rule_unspent_skill_points(_Snap(
        game_time=30.0,
        active_combat=_state(hp_pct=1.0, unspent_skill_points=1),
    ))
    assert rec is None


def test_skill_point_rule_pluralizes_correctly() -> None:
    rec_single = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=1.0, unspent_skill_points=1),
    ))
    rec_multi = rule_unspent_skill_points(_Snap(
        game_time=300.0,
        active_combat=_state(hp_pct=1.0, unspent_skill_points=3),
    ))
    assert rec_single is not None and rec_multi is not None
    assert "Punkt offen" in rec_single.text
    assert "Punkte offen" in rec_multi.text


# Skill-point suppression
def test_skill_point_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("skill_point_unspent", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "skill_point_unspent" for r in out)


def test_skill_point_suppressed_by_numbers_disadv() -> None:
    recs = [_rec("numbers_disadv", "warn"), _rec("skill_point_unspent", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "skill_point_unspent" for r in out)


def test_skill_point_suppressed_by_ally_inhib_down() -> None:
    recs = [_rec("ally_inhib_down", "alert"), _rec("skill_point_unspent", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "skill_point_unspent" for r in out)


def test_skill_point_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("skill_point_unspent", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "skill_point_unspent" for r in out)


def test_skill_point_survives_normal_tilt() -> None:
    """Non-spiral tilt (warn) shouldn't kill the micro-nag."""
    recs = [_rec("tilt", "warn"), _rec("skill_point_unspent", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "skill_point_unspent" for r in out)
