"""Tests for ``advisor.decision_engine._win_path_tagger``.

Pins the rule-kind → win_path mapping + the matchup-aware dynamic
routing for build_swap / situational_build. Coverage:
  * Static map round-trip per category bucket.
  * Dynamic routing flips threat_response ↔ primary_path based on
    WinCondition.raw_tags.
  * Pre-tagged recs aren't overwritten.
  * Unknown / empty kinds yield empty win_path (UI hides anchor).
"""
from __future__ import annotations

from champ_assistant.advisor.decision_engine._core import Recommendation
from champ_assistant.advisor.decision_engine._win_path_tagger import (
    WIN_PATH_AVOID,
    WIN_PATH_CLOSING,
    WIN_PATH_PRIMARY,
    WIN_PATH_SPIKE,
    WIN_PATH_THREAT,
    tag_recommendation,
)
from champ_assistant.advisor.win_condition import WinCondition


def _rec(kind: str, **kw) -> Recommendation:  # type: ignore[no-untyped-def]
    """Lightweight Recommendation builder for tests."""
    return Recommendation(
        text=kw.pop("text", "x"),
        severity=kw.pop("severity", "info"),
        category=kw.pop("category", "tempo"),
        kind=kind,
        **kw,
    )


def _wc(raw_tags: tuple[str, ...] = ()) -> WinCondition:
    """Minimal WinCondition with just the raw_tags driver populated."""
    return WinCondition(
        headline="Test plan",
        primary_path="Test path",
        spikes=("Test spike",),
        threats=("Test threat",),
        avoid="Test avoid",
        archetype_label="Test",
        raw_tags=raw_tags,
    )


# ── Static map: each bucket has at least one rule routed to it ───────────

def test_ace_detected_routes_to_closing_window() -> None:
    out = tag_recommendation(_rec("ace_detected"), _wc())
    assert out.win_path == WIN_PATH_CLOSING


def test_kill_lead_snowball_routes_to_closing_window() -> None:
    out = tag_recommendation(_rec("kill_lead_snowball"), _wc())
    assert out.win_path == WIN_PATH_CLOSING


def test_power_spike_routes_to_spike_window() -> None:
    out = tag_recommendation(_rec("power_spike"), _wc())
    assert out.win_path == WIN_PATH_SPIKE


def test_enemy_item_spike_routes_to_spike_window() -> None:
    out = tag_recommendation(_rec("enemy_item_spike"), _wc())
    assert out.win_path == WIN_PATH_SPIKE


def test_gank_risk_routes_to_threat_response() -> None:
    out = tag_recommendation(_rec("gank_risk"), _wc())
    assert out.win_path == WIN_PATH_THREAT


def test_enemy_baron_buff_routes_to_threat_response() -> None:
    out = tag_recommendation(_rec("enemy_baron_buff"), _wc())
    assert out.win_path == WIN_PATH_THREAT


def test_numbers_disadvantage_routes_to_avoid() -> None:
    out = tag_recommendation(_rec("numbers_disadvantage"), _wc())
    assert out.win_path == WIN_PATH_AVOID


def test_far_behind_safe_routes_to_avoid() -> None:
    out = tag_recommendation(_rec("far_behind_safe"), _wc())
    assert out.win_path == WIN_PATH_AVOID


def test_recall_check_routes_to_primary_path() -> None:
    out = tag_recommendation(_rec("recall_check"), _wc())
    assert out.win_path == WIN_PATH_PRIMARY


def test_baron_window_routes_to_primary_path() -> None:
    """Window calls are objective tempo, not closing — they only become
    closing when the corresponding buff actually drops."""
    out = tag_recommendation(_rec("baron_window"), _wc())
    assert out.win_path == WIN_PATH_PRIMARY


def test_drake_give_up_routes_to_avoid() -> None:
    """Give-up rules are explicit "don't do this" calls."""
    out = tag_recommendation(_rec("drake_give_up"), _wc())
    assert out.win_path == WIN_PATH_AVOID


# ── Dynamic routing: build_swap + situational_build ──────────────────────

def test_build_swap_with_threat_signal_routes_to_threat_response() -> None:
    """Matchup carries any threat tag → swap is a counter-play, not
    just tempo."""
    wc = _wc(raw_tags=("burst_threat",))
    out = tag_recommendation(_rec("build_swap"), wc)
    assert out.win_path == WIN_PATH_THREAT


def test_build_swap_without_threat_signal_routes_to_primary_path() -> None:
    """No matchup signal → swap is a generic gold-efficiency call."""
    wc = _wc(raw_tags=("scaling_team",))
    out = tag_recommendation(_rec("build_swap"), wc)
    assert out.win_path == WIN_PATH_PRIMARY


def test_situational_build_with_any_raw_tags_routes_to_threat_response() -> None:
    wc = _wc(raw_tags=("ap_heavy_enemy",))
    out = tag_recommendation(_rec("situational_build"), wc)
    assert out.win_path == WIN_PATH_THREAT


def test_situational_build_without_raw_tags_routes_to_primary_path() -> None:
    wc = _wc(raw_tags=())
    out = tag_recommendation(_rec("situational_build"), wc)
    assert out.win_path == WIN_PATH_PRIMARY


def test_dynamic_routing_with_no_win_condition_falls_back_to_static() -> None:
    """No WinCondition → no tags context → both build rules fall to
    primary_path."""
    swap_out = tag_recommendation(_rec("build_swap"), None)
    sit_out = tag_recommendation(_rec("situational_build"), None)
    assert swap_out.win_path == WIN_PATH_PRIMARY
    assert sit_out.win_path == WIN_PATH_PRIMARY


# ── Defensive paths ──────────────────────────────────────────────────────

def test_unknown_kind_yields_empty_win_path() -> None:
    """Unmapped rule kinds emit no anchor — UI hides the line."""
    out = tag_recommendation(_rec("never_added_to_map"), _wc())
    assert out.win_path == ""


def test_empty_kind_yields_empty_win_path() -> None:
    out = tag_recommendation(_rec(""), _wc())
    assert out.win_path == ""


def test_pre_tagged_recommendation_is_returned_unchanged() -> None:
    """If a rule explicitly stamps win_path (custom routing), the
    tagger respects that — never overwrites an explicit choice."""
    rec = _rec("ace_detected", win_path="custom_value")
    out = tag_recommendation(rec, _wc())
    assert out.win_path == "custom_value"


def test_recommendation_returned_is_a_copy_not_mutated() -> None:
    """Recommendation is frozen — tagger must use ``replace`` rather
    than try to mutate. Verify the returned instance is a NEW dataclass
    so callers see the new tag without side-effecting the input."""
    rec = _rec("ace_detected")
    out = tag_recommendation(rec, _wc())
    assert out is not rec  # new instance via dataclasses.replace
    assert rec.win_path == ""  # input unchanged
    assert out.win_path == WIN_PATH_CLOSING


# ── evaluate-level integration ──────────────────────────────────────────

def test_evaluate_tags_recs_when_win_condition_provided() -> None:
    """End-to-end: evaluate(snapshot, win_condition=...) returns recs
    with their win_path populated."""
    from dataclasses import dataclass, field as dc_field

    from champ_assistant.advisor.build_engine import BuildResult, BuildSwap
    from champ_assistant.advisor.decision_engine import evaluate

    @dataclass
    class _Snap:
        game_time: float = 300.0
        allies: list = dc_field(default_factory=list)
        enemies: list = dc_field(default_factory=list)
        objectives: list = dc_field(default_factory=list)
        raw_events: list = dc_field(default_factory=list)

    swap = BuildSwap(
        skip_item="Bloodthirster", skip_item_id=3072,
        replacement="Mortal Reminder", replacement_id=3033,
        reason="vs 2 Sustain-Gegner",
        score_delta=44.0,
    )
    from champ_assistant.advisor.build_engine import (
        ChampionArchetype,
    )
    arch = ChampionArchetype(
        damage_type="magic", item_damage_type="magic",
        play_style="mage", is_ranged=True, has_mana=True,
        primary_position="MIDDLE", scaling_attributes=frozenset(),
    )
    result = BuildResult(
        champion_name="Ahri", archetype=arch,
        core_items=(), situational_items=(),
        boots_name=None, boots_id=None,
        starter_name=None, starter_id=None,
        swap_suggestions=(swap,),
    )
    wc = _wc(raw_tags=("sustain_threat",))
    recs = evaluate(_Snap(), situational_build=result, win_condition=wc)
    swap_recs = [r for r in recs if r.kind == "build_swap"]
    assert swap_recs, "rule_build_swap should have fired"
    assert swap_recs[0].win_path == WIN_PATH_THREAT


def test_evaluate_without_win_condition_leaves_win_path_empty() -> None:
    """Backwards compatibility — calling evaluate() without
    win_condition produces untagged recs (UI hides the anchor)."""
    from dataclasses import dataclass, field as dc_field
    from champ_assistant.advisor.decision_engine import evaluate

    @dataclass
    class _Snap:
        game_time: float = 300.0
        allies: list = dc_field(default_factory=list)
        enemies: list = dc_field(default_factory=list)
        objectives: list = dc_field(default_factory=list)
        raw_events: list = dc_field(default_factory=list)

    recs = evaluate(_Snap())
    for r in recs:
        assert r.win_path == "", f"untagged rec carried win_path={r.win_path}"
