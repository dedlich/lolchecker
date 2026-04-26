"""Unit tests for analyze_composition."""
from __future__ import annotations

from champ_assistant.advisor.composition import analyze_composition
from champ_assistant.data.models import TagsData


def test_empty_team_has_all_critical_gaps() -> None:
    gaps = analyze_composition([], TagsData(tags={}))
    categories = {g.category for g in gaps}
    assert "frontline" in categories
    assert "engage" in categories
    # No damage check is symmetric: when both AD and AP are missing, neither
    # specific gap fires — we just register frontline + engage + peel + waveclear.
    assert "peel" in categories
    assert "wave_clear" in categories


def test_balanced_comp_has_no_gaps() -> None:
    tags = TagsData(
        tags={
            "Garen": ["Tank", "Engage"],
            "Lee Sin": ["Fighter", "Diver"],
            "Annie": ["Mage", "Burst", "Crowd-Control"],
            "Caitlyn": ["Marksman", "Wave-Clear"],
            "Lulu": ["Enchanter", "Peel"],
        }
    )
    gaps = analyze_composition(["Garen", "Lee Sin", "Annie", "Caitlyn", "Lulu"], tags)
    categories = {g.category for g in gaps}
    assert categories == set()  # nothing missing


def test_no_frontline_is_critical() -> None:
    tags = TagsData(
        tags={
            "Caitlyn": ["Marksman"],
            "Annie": ["Mage", "Burst"],
            "Ezreal": ["Marksman"],
            "Lulu": ["Enchanter"],
            "Yasuo": ["Assassin"],
        }
    )
    gaps = analyze_composition(["Caitlyn", "Annie", "Ezreal", "Lulu", "Yasuo"], tags)
    frontline_gaps = [g for g in gaps if g.category == "frontline"]
    assert len(frontline_gaps) == 1
    assert frontline_gaps[0].severity == "critical"


def test_only_ad_team_misses_ap() -> None:
    tags = TagsData(
        tags={
            "Garen": ["Tank", "Engage"],
            "Lee Sin": ["Fighter"],
            "Caitlyn": ["Marksman"],
        }
    )
    gaps = analyze_composition(["Garen", "Lee Sin", "Caitlyn"], tags)
    cats = {g.category for g in gaps}
    assert "ap_damage" in cats
    assert "ad_damage" not in cats


def test_only_ap_team_misses_ad() -> None:
    tags = TagsData(
        tags={
            "Galio": ["Mage", "Tank", "Engage"],
            "Annie": ["Mage", "Burst", "Crowd-Control"],
            "Lulu": ["Enchanter", "Peel"],
            "Soraka": ["Enchanter"],
            "Vex": ["Mage", "Burst"],
        }
    )
    gaps = analyze_composition(["Galio", "Annie", "Lulu", "Soraka", "Vex"], tags)
    cats = {g.category for g in gaps}
    assert "ad_damage" in cats
    assert "ap_damage" not in cats


def test_gaps_sorted_critical_first() -> None:
    tags = TagsData(tags={})  # everything missing
    gaps = analyze_composition([], tags)
    severities = [g.severity for g in gaps]
    # All critical / important gaps come before nice_to_have.
    assert severities == sorted(
        severities,
        key=lambda s: {"critical": 0, "important": 1, "nice_to_have": 2}[s],
    )


def test_unknown_champions_treated_as_no_tags() -> None:
    tags = TagsData(tags={})
    gaps = analyze_composition(["UnknownChamp"], tags)
    # Same gaps as empty team — unknown champs contribute no tag coverage.
    assert any(g.category == "frontline" for g in gaps)
