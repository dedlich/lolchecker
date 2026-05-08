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
