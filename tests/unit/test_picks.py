"""Unit tests for suggest_picks."""
from __future__ import annotations

import pytest

from champ_assistant.advisor.composition import CompositionGap
from champ_assistant.advisor.picks import suggest_picks
from champ_assistant.data.models import (
    CounterEntry,
    CounterMatrix,
    TagsData,
    TierEntry,
    TierList,
)


def _tiers(role: str, entries: list[tuple[str, str]]) -> TierList:
    return TierList(tiers={role: [TierEntry(champion=c, tier=t) for c, t in entries]})  # type: ignore[dict-item]


def test_returns_top_tier_first() -> None:
    tiers = _tiers("TOP", [("Darius", "S+"), ("Garen", "A"), ("Sett", "B")])
    suggestions = suggest_picks(
        "TOP", [], [], [], tiers, CounterMatrix(), TagsData()
    )
    keys = [s.champion_key for s in suggestions]
    assert keys[0] == "Darius"
    assert suggestions[0].score >= suggestions[-1].score


def test_excludes_already_drafted_champions() -> None:
    tiers = _tiers("TOP", [("Darius", "S+"), ("Garen", "A")])
    suggestions = suggest_picks(
        "TOP", ["Darius"], [], [], tiers, CounterMatrix(), TagsData()
    )
    keys = [s.champion_key for s in suggestions]
    assert "Darius" not in keys
    assert "Garen" in keys


def test_excludes_enemy_picks() -> None:
    tiers = _tiers("TOP", [("Darius", "S+"), ("Garen", "A")])
    suggestions = suggest_picks(
        "TOP", [], ["Darius"], [], tiers, CounterMatrix(), TagsData()
    )
    keys = [s.champion_key for s in suggestions]
    assert "Darius" not in keys


def test_counter_boost_reorders_results() -> None:
    tiers = _tiers("TOP", [("Garen", "S+"), ("Darius", "A")])
    counters = CounterMatrix(
        matrix={
            "Yasuo": {"TOP": [CounterEntry(champion="Darius", score=10.0)]},
        }
    )
    # Without enemy: Garen S+ wins.
    base = suggest_picks("TOP", [], [], [], tiers, CounterMatrix(), TagsData())
    assert base[0].champion_key == "Garen"
    # With Yasuo as enemy: Darius's counter boost flips the order.
    boosted = suggest_picks("TOP", [], ["Yasuo"], [], tiers, counters, TagsData())
    assert boosted[0].champion_key == "Darius"
    assert any("counters Yasuo" in r for r in boosted[0].reasons)


def test_gap_fill_bonus() -> None:
    tiers = _tiers("TOP", [("Camille", "A"), ("Garen", "A")])
    tags = TagsData(
        tags={"Camille": ["Fighter", "Diver"], "Garen": ["Tank", "Engage"]}
    )
    gaps = [
        CompositionGap(
            category="frontline",
            severity="critical",
            description="need a tank",
        )
    ]
    suggestions = suggest_picks(
        "TOP", [], [], gaps, tiers, CounterMatrix(), tags
    )
    # Garen's "Tank" tag covers the frontline gap; Camille has Fighter/Diver
    # which is also frontline-coded — both get the bonus, but ordering is
    # then deterministic by champion key.
    assert suggestions[0].score > 15.0  # tier-only baseline
    fill_reasons = [r for s in suggestions for r in s.reasons if "fills" in r]
    assert fill_reasons


def test_score_clamped_to_100() -> None:
    tiers = _tiers("TOP", [("X", "S+")])
    counters = CounterMatrix(
        matrix={
            f"E{i}": {"TOP": [CounterEntry(champion="X", score=10.0)]} for i in range(5)
        }
    )
    suggestions = suggest_picks(
        "TOP", [], [f"E{i}" for i in range(5)], [], tiers, counters, TagsData()
    )
    assert suggestions[0].score <= 100.0


def test_limit_truncates_output() -> None:
    tiers = _tiers(
        "TOP",
        [("A", "S+"), ("B", "S"), ("C", "A"), ("D", "B"), ("E", "C")],
    )
    suggestions = suggest_picks(
        "TOP", [], [], [], tiers, CounterMatrix(), TagsData(), limit=2
    )
    assert len(suggestions) == 2


def test_negative_limit_raises() -> None:
    tiers = _tiers("TOP", [("A", "S+")])
    with pytest.raises(ValueError, match="non-negative"):
        suggest_picks("TOP", [], [], [], tiers, CounterMatrix(), TagsData(), limit=-1)


def test_empty_tier_list_yields_no_suggestions() -> None:
    suggestions = suggest_picks(
        "TOP", [], [], [], TierList(), CounterMatrix(), TagsData()
    )
    assert suggestions == []


def test_blank_enemy_keys_are_ignored() -> None:
    tiers = _tiers("TOP", [("Garen", "S+")])
    counters = CounterMatrix(
        matrix={"Yasuo": {"TOP": [CounterEntry(champion="Garen", score=10.0)]}}
    )
    suggestions = suggest_picks(
        "TOP", [], ["", "Yasuo", ""], [], tiers, counters, TagsData()
    )
    assert any("counters Yasuo" in r for r in suggestions[0].reasons)
