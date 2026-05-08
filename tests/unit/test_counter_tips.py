"""Unit tests for ``advisor.counter_tips.counter_tip_for_tags``.

Drives the LiveCompanion enemy-portrait tooltip. Tag priority is
specificity-first — Assassin / Hyper-Carry / Lane-Bully etc beat
generic role tags.
"""
from __future__ import annotations

from champ_assistant.advisor.counter_tips import counter_tip_for_tags


def test_empty_tags_returns_empty_string() -> None:
    """No data → no tip. Lets the UI omit the tooltip rather than
    show a generic placeholder."""
    assert counter_tip_for_tags([]) == ""


def test_assassin_returns_burst_tip() -> None:
    tip = counter_tip_for_tags(["Assassin", "Mage"])
    assert "burst" in tip.lower()
    assert "distance" in tip.lower()


def test_assassin_overrides_mage() -> None:
    """Specificity-first: Assassin tip wins over the generic Mage tip
    even when both tags are present (e.g. Ahri / LeBlanc)."""
    assassin_tip = counter_tip_for_tags(["Assassin", "Mage"])
    mage_tip = counter_tip_for_tags(["Mage"])
    assert assassin_tip != mage_tip
    assert "burst" in assassin_tip.lower()


def test_hyper_carry_overrides_marksman() -> None:
    """Hyper-Carry tip is more actionable than the generic Marksman
    tip — Vayne should show "outscales" not "ranged DPS"."""
    hyper_tip = counter_tip_for_tags(["Marksman", "Hyper-Carry"])
    assert "outscale" in hyper_tip.lower() or "early" in hyper_tip.lower()


def test_lane_bully_overrides_early_game() -> None:
    """Both tags mean "strong early" but Lane-Bully is the more
    specific snowball-prone signal."""
    bully_tip = counter_tip_for_tags(["Lane-Bully", "Early-Game", "Fighter"])
    assert "snowball" in bully_tip.lower() or "play safe" in bully_tip.lower()


def test_late_game_tip() -> None:
    tip = counter_tip_for_tags(["Late-Game", "Mage"])
    assert "outscale" in tip.lower()


def test_marksman_only_returns_ranged_dps_tip() -> None:
    """Generic role tag falls through to the basic role tip when no
    more-specific tag matches."""
    tip = counter_tip_for_tags(["Marksman"])
    assert "ranged" in tip.lower()


def test_tank_returns_focus_carry_tip() -> None:
    tip = counter_tip_for_tags(["Tank", "Fighter"])
    # Tank is more specific than Fighter — Tank wins.
    assert "carries" in tip.lower() or "peel" in tip.lower()


def test_unknown_tag_falls_through_to_empty() -> None:
    """Tag we don't have a rule for → empty string. The UI handles
    empty gracefully (no tooltip)."""
    assert counter_tip_for_tags(["FuturePatchTag"]) == ""


def test_tip_is_short_enough_for_tooltip() -> None:
    """Cheap sanity check that no tip exceeds ~80 chars — Qt
    tooltips don't word-wrap by default and a long line would
    extend off-screen."""
    from champ_assistant.advisor.counter_tips import _TIP_RULES
    for tag, tip in _TIP_RULES:
        assert len(tip) <= 90, f"tip for {tag} is too long ({len(tip)} chars): {tip!r}"
