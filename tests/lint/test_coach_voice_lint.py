"""Voice-quality lint for coach_voice + the rules that use it.

Pins the editorial contract — every rec produced by a coach_voice-
backed rule must:

  * Fit ``coach_voice.MAX_LENGTH`` (60 chars) so it renders on one
    line in-game without wrapping or truncation.
  * Carry an imperative marker (urgency token, leading verb, or
    all-caps action). Status-report phrasing is rejected.

The lint hits the rule SURFACE (final ``Recommendation.text`` output),
not the source file, because the rules build text dynamically from
runtime data. We wire each rule with a representative
``BuildResult`` / ``LcdaSnapshot`` and inspect what comes out.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pytest

from champ_assistant.advisor import coach_voice
from champ_assistant.advisor.build_engine import (
    BuildResult,
    BuildSwap,
    ChampionArchetype,
    ScoredItem,
)
from champ_assistant.advisor.decision_engine import (
    rule_build_swap,
    rule_situational_build,
)


@dataclass
class _Snap:
    game_time: float = 600.0
    allies: list = dc_field(default_factory=list)
    enemies: list = dc_field(default_factory=list)
    objectives: list = dc_field(default_factory=list)
    raw_events: list = dc_field(default_factory=list)


def _arch() -> ChampionArchetype:
    return ChampionArchetype(
        damage_type="magic",
        item_damage_type="magic",
        play_style="mage",
        is_ranged=True,
        has_mana=True,
        primary_position="MIDDLE",
        scaling_attributes=frozenset(),
    )


def _build_result(
    *,
    situational_items: tuple[ScoredItem, ...] = (),
    swap_suggestions: tuple[BuildSwap, ...] = (),
) -> BuildResult:
    return BuildResult(
        champion_name="Ahri",
        archetype=_arch(),
        core_items=(),
        situational_items=situational_items,
        boots_name=None,
        boots_id=None,
        starter_name=None,
        starter_id=None,
        swap_suggestions=swap_suggestions,
    )


# ── coach_voice.directive guarantees ──────────────────────────────────────

def test_directive_enforces_length_cap_when_action_overruns() -> None:
    """When even the BARE action exceeds MAX_LENGTH (no consequence to
    drop), the helper raises rather than silently emitting a clipped
    line. Forces the calling rule to use a shorter directive instead."""
    over_long_action = "Recall " + "x" * 80  # 87 chars
    with pytest.raises(ValueError, match="exceeds"):
        coach_voice.directive(over_long_action)


def test_directive_drops_consequence_when_only_consequence_overflows() -> None:
    """When the action fits but adding the consequence pushes over the
    cap, the helper keeps the action and drops the parenthetical.
    Better truncated WHY than a clipped WHAT."""
    long_consequence = "weil " + "very " * 50 + "long"
    out = coach_voice.directive(
        "Mortal Reminder",
        consequence=long_consequence,
        urgency="now",
    )
    assert out == "Mortal Reminder JETZT"
    assert "(" not in out


def test_directive_drops_consequence_to_fit() -> None:
    """Borderline case: action fits, consequence pushes past 60. The
    helper should keep the action and drop the consequence rather
    than truncate mid-sentence."""
    out = coach_voice.directive(
        "Recall",
        consequence=("a" * 80),
        urgency="now",
    )
    # No parens — consequence dropped.
    assert "(" not in out
    assert out == "Recall JETZT"


def test_directive_rejects_passive_voice() -> None:
    """Status-report phrasing without an imperative marker is rejected
    so we don't quietly emit "you might want to ..."  -style lines."""
    with pytest.raises(ValueError, match="imperative"):
        coach_voice.directive("vielleicht später kaufen")


def test_directive_accepts_recognized_verbs() -> None:
    assert coach_voice.directive("Recall", consequence="HP zu niedrig") == \
        "Recall (HP zu niedrig)"
    assert coach_voice.directive("Push") == "Push"
    assert coach_voice.directive("Drache holen") == "Drache holen"


def test_status_line_keeps_facts_then_action() -> None:
    text = coach_voice.status_line("Wir 4v5", action="keine Fights bis Respawn")
    assert text == "Wir 4v5 — keine Fights bis Respawn"


def test_status_line_rejects_passive_action_half() -> None:
    with pytest.raises(ValueError, match="imperative"):
        coach_voice.status_line(
            "Drake spawnt in 30s",
            action="ich denke wir sollten warten",
        )


# ── Rule SURFACE pins — final Recommendation.text ─────────────────────────

def test_rule_situational_build_surface_is_directive() -> None:
    """The 2-min situational call must produce a coach-voice line —
    fits MAX_LENGTH AND carries an imperative marker."""
    snap = _Snap(game_time=300.0)
    item = ScoredItem(
        item_id=3157,
        item_name="Zhonya's Hourglass",
        score=120.0,
        reasons=("+30 vs 2 Burst-Gegner (Anti-Burst)",),
    )
    result = _build_result(situational_items=(item,))
    rec = rule_situational_build(snap, result)
    assert rec is not None
    assert len(rec.text) <= coach_voice.MAX_LENGTH, (
        f"text exceeds in-game width: {rec.text!r} "
        f"({len(rec.text)} chars)"
    )
    assert "JETZT" in rec.text  # urgency marker present
    assert "Zhonya" in rec.text


def test_rule_build_swap_surface_is_directive() -> None:
    snap = _Snap(game_time=300.0)
    swap = BuildSwap(
        skip_item="Bloodthirster",
        skip_item_id=3072,
        replacement="Mortal Reminder",
        replacement_id=3033,
        reason="vs 2 Sustain-Gegner (GW)",
        score_delta=44.0,
    )
    result = _build_result(swap_suggestions=(swap,))
    rec = rule_build_swap(snap, result)
    assert rec is not None
    assert len(rec.text) <= coach_voice.MAX_LENGTH, (
        f"text exceeds in-game width: {rec.text!r}"
    )
    assert "JETZT" in rec.text
    assert "Mortal Reminder" in rec.text
    # Skip-item context preserved in the reason chain.
    assert any("Bloodthirster" in r for r in rec.reasons)


def test_rule_build_swap_short_item_name_keeps_consequence() -> None:
    """Short item name + short consequence → both should fit. Pin the
    "<replacement> JETZT (...)" shape so the line still teaches WHY."""
    snap = _Snap(game_time=300.0)
    swap = BuildSwap(
        skip_item="BT",
        skip_item_id=3072,
        replacement="QSS",  # short name leaves room for parenthetical
        replacement_id=3140,
        reason="vs 3 CC",
        score_delta=30.0,
    )
    result = _build_result(swap_suggestions=(swap,))
    rec = rule_build_swap(snap, result)
    assert rec is not None
    assert "QSS" in rec.text
    assert "JETZT" in rec.text


# ── Certified-rules registry ─────────────────────────────────────────────
#
# Every rule in this list is pinned to coach_voice.validate() — the
# parametrized test below fires the rule with a representative snapshot
# and asserts the output text passes the voice contract. Adding a rule
# here is the deliberate "this rule is coach-voice certified" step;
# breaking the voice contract on a certified rule fails CI.
#
# Two registered already via the more focused tests above
# (rule_situational_build, rule_build_swap). Below registry covers
# the rules that build text dynamically from snapshot data —
# certifying them required first trimming any text that overran the
# 60-char limit (recall_check paths 3 + 4 in v1.10.130).


def _make_combat_state(
    *, hp_pct: float = 0.7, mana_pct: float = 0.8,
    gold: float = 500.0, is_mana_user: bool = True,
) -> object:
    """Minimal active-combat state for personal-rule fixtures."""
    @dataclass
    class _State:
        hp_pct: float = 0.7
        mana_pct: float = 0.8
        gold: float = 500.0
        is_mana_user: bool = True
    return _State(hp_pct=hp_pct, mana_pct=mana_pct, gold=gold, is_mana_user=is_mana_user)


@dataclass
class _Player:
    """Snapshot player fixture. ``respawn_timer`` defaults to 0.0 (not
    None) so ``_alive_count`` treats the snapshot as carrying live
    respawn data — without that the helper falls back to
    ``len(players)`` and the alive-count differential rules can't
    fire from the fixture."""
    is_alive: bool = True
    respawn_timer: float = 0.0
    champion_name: str = ""


def _dead_player(*, respawn_timer: float = 15.0, champion_name: str = "") -> _Player:
    return _Player(is_alive=False, respawn_timer=respawn_timer, champion_name=champion_name)


@dataclass
class _SnapWithPlayers:
    """Snapshot fixture that drives combat + recall + power-spike rules."""
    game_time: float = 600.0
    allies: list = dc_field(default_factory=list)
    enemies: list = dc_field(default_factory=list)
    objectives: list = dc_field(default_factory=list)
    raw_events: list = dc_field(default_factory=list)
    new_spikes: list = dc_field(default_factory=list)
    enemy_spikes: list = dc_field(default_factory=list)
    active_combat: object = None
    active_summoner: str = ""
    ally_aggregate: object = None
    enemy_aggregate: object = None


def test_rule_recall_check_critical_hp_passes_voice() -> None:
    """Path 1 — HP < 30 % → "RECALL JETZT, ein Trade tötet dich"-style."""
    from champ_assistant.advisor.decision_engine import (
        reset_recall_hysteresis, rule_recall_check,
    )
    reset_recall_hysteresis()
    state = _make_combat_state(hp_pct=0.7)  # arm hysteresis above HP_RECALL_REARM
    rule_recall_check(_SnapWithPlayers(active_combat=state))  # arm pass
    state2 = _make_combat_state(hp_pct=0.20, gold=300.0)
    rec = rule_recall_check(_SnapWithPlayers(active_combat=state2))
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_recall_check_resource_path_passes_voice() -> None:
    """Path 2 — HP/mana low + back-worth gold."""
    from champ_assistant.advisor.decision_engine import (
        reset_recall_hysteresis, rule_recall_check,
    )
    reset_recall_hysteresis()
    state_arm = _make_combat_state(hp_pct=0.7, mana_pct=0.8, gold=400.0)
    rule_recall_check(_SnapWithPlayers(active_combat=state_arm))
    state_fire = _make_combat_state(hp_pct=0.45, mana_pct=0.20, gold=1100.0)
    rec = rule_recall_check(_SnapWithPlayers(active_combat=state_fire))
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_recall_check_gold_path_passes_voice() -> None:
    """Path 3 — gold ≥ Component-Spike threshold + safe HP. Fixed
    in v1.10.130: was 62 chars (overran MAX_LENGTH)."""
    from champ_assistant.advisor.decision_engine import (
        reset_recall_hysteresis, rule_recall_check,
    )
    reset_recall_hysteresis()
    state_arm = _make_combat_state(hp_pct=0.9, gold=500.0)
    rule_recall_check(_SnapWithPlayers(active_combat=state_arm))
    state_fire = _make_combat_state(hp_pct=0.9, gold=1300.0)
    rec = rule_recall_check(_SnapWithPlayers(active_combat=state_fire))
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_recall_check_mana_path_passes_voice() -> None:
    """Path 4 — mana < depleted threshold. Fixed in v1.10.130: was 63
    chars (overran MAX_LENGTH)."""
    from champ_assistant.advisor.decision_engine import (
        reset_recall_hysteresis, rule_recall_check,
    )
    reset_recall_hysteresis()
    state_arm = _make_combat_state(hp_pct=0.95, mana_pct=0.8)
    rule_recall_check(_SnapWithPlayers(active_combat=state_arm))
    state_fire = _make_combat_state(hp_pct=0.95, mana_pct=0.10)
    rec = rule_recall_check(_SnapWithPlayers(active_combat=state_fire))
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_numbers_disadvantage_passes_voice() -> None:
    """4v5 — KEINE Fights bis Respawn."""
    from champ_assistant.advisor.decision_engine import rule_numbers_disadvantage
    snap = _SnapWithPlayers(
        allies=[_dead_player()] + [_Player()] * 4,
        enemies=[_Player()] * 5,
    )
    rec = rule_numbers_disadvantage(snap)
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_numbers_advantage_passes_voice() -> None:
    """5v3 — JETZT Pressure, Obj forcen!"""
    from champ_assistant.advisor.decision_engine import rule_numbers_advantage
    snap = _SnapWithPlayers(
        allies=[_Player()] * 5,
        enemies=[_dead_player(), _dead_player()] + [_Player()] * 3,
    )
    rec = rule_numbers_advantage(snap)
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_ace_detected_passes_voice() -> None:
    """ACE! Alle 5 Feinde tot — PUSHEN zum GG!"""
    from champ_assistant.advisor.decision_engine import rule_ace_detected
    snap = _SnapWithPlayers(
        allies=[_Player()] * 5,
        enemies=[_dead_player()] * 5,
    )
    rec = rule_ace_detected(snap)
    assert rec is not None
    coach_voice.validate(rec.text)


def test_rule_fight_window_closing_passes_voice() -> None:
    """Jetzt pushen — Yasuo zurück in 5s!"""
    from champ_assistant.advisor.decision_engine import rule_fight_window_closing
    snap = _SnapWithPlayers(
        allies=[_Player()] * 5,
        enemies=[_dead_player(respawn_timer=5.0, champion_name="Yasuo")]
                + [_Player()] * 4,
    )
    rec = rule_fight_window_closing(snap)
    assert rec is not None
    coach_voice.validate(rec.text)
