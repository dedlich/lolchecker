"""Unit tests for find_counters."""
from __future__ import annotations

import pytest

from champ_assistant.advisor.counters import find_counters
from champ_assistant.data.models import CounterEntry, CounterMatrix


def _matrix(entries: dict[str, dict[str, list[tuple[str, float]]]]) -> CounterMatrix:
    return CounterMatrix(
        matrix={
            enemy: {
                role: [CounterEntry(champion=c, score=s) for c, s in counters]
                for role, counters in roles.items()
            }
            for enemy, roles in entries.items()
        }
    )


def test_returns_empty_for_unknown_enemy() -> None:
    cm = _matrix({"Garen": {"TOP": [("Darius", 8.0)]}})
    assert find_counters("Yasuo", "TOP", cm) == []


def test_returns_empty_for_unknown_role() -> None:
    cm = _matrix({"Garen": {"TOP": [("Darius", 8.0)]}})
    assert find_counters("Garen", "MID", cm) == []


def test_returns_empty_for_blank_enemy() -> None:
    cm = _matrix({"Garen": {"TOP": [("Darius", 8.0)]}})
    assert find_counters("", "TOP", cm) == []


def test_sorts_by_score_descending() -> None:
    cm = _matrix(
        {"Garen": {"TOP": [("Darius", 6.0), ("Vayne", 9.0), ("Quinn", 7.0)]}}
    )
    keys = [c.champion for c in find_counters("Garen", "TOP", cm)]
    assert keys == ["Vayne", "Quinn", "Darius"]


def test_filters_out_enemy_self() -> None:
    cm = _matrix({"Garen": {"TOP": [("Garen", 9.9), ("Darius", 8.0)]}})
    keys = [c.champion for c in find_counters("Garen", "TOP", cm)]
    assert "Garen" not in keys
    assert keys == ["Darius"]


def test_limit_truncates_results() -> None:
    cm = _matrix(
        {"Garen": {"TOP": [("A", 5), ("B", 6), ("C", 7), ("D", 8), ("E", 9)]}}
    )
    keys = [c.champion for c in find_counters("Garen", "TOP", cm, limit=3)]
    assert keys == ["E", "D", "C"]


def test_limit_zero_returns_empty() -> None:
    cm = _matrix({"Garen": {"TOP": [("Darius", 8.0)]}})
    assert find_counters("Garen", "TOP", cm, limit=0) == []


def test_negative_limit_raises() -> None:
    cm = _matrix({"Garen": {"TOP": [("Darius", 8.0)]}})
    with pytest.raises(ValueError, match="non-negative"):
        find_counters("Garen", "TOP", cm, limit=-1)
