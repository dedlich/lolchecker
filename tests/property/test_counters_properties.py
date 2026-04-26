"""Property-based tests for find_counters."""
from __future__ import annotations

from hypothesis import given, strategies as st

from champ_assistant.advisor.counters import find_counters
from champ_assistant.data.models import CounterMatrix

from .conftest import champion_keys, counter_matrix, roles


@given(matrix=counter_matrix(), enemy=champion_keys, role=roles)
def test_never_returns_enemy_self(matrix: CounterMatrix, enemy: str, role: str) -> None:
    """A counter list against ``enemy`` must never contain ``enemy``."""
    counters = find_counters(enemy, role, matrix)  # type: ignore[arg-type]
    for c in counters:
        assert c.champion != enemy


@given(matrix=counter_matrix(), enemy=champion_keys, role=roles)
def test_sorted_by_score_descending(
    matrix: CounterMatrix, enemy: str, role: str
) -> None:
    counters = find_counters(enemy, role, matrix)  # type: ignore[arg-type]
    scores = [c.score for c in counters]
    assert scores == sorted(scores, reverse=True)


@given(
    matrix=counter_matrix(),
    enemy=champion_keys,
    role=roles,
    limit=st.integers(min_value=0, max_value=20),
)
def test_respects_limit(
    matrix: CounterMatrix, enemy: str, role: str, limit: int
) -> None:
    counters = find_counters(enemy, role, matrix, limit=limit)  # type: ignore[arg-type]
    assert len(counters) <= limit


@given(matrix=counter_matrix(), enemy=champion_keys, role=roles)
def test_result_is_subset_of_input(
    matrix: CounterMatrix, enemy: str, role: str
) -> None:
    """Every entry in the result existed in the input matrix (no fabrication)."""
    raw = matrix.counters_for(enemy, role)  # type: ignore[arg-type]
    raw_keys = {c.champion for c in raw}
    counters = find_counters(enemy, role, matrix)  # type: ignore[arg-type]
    for c in counters:
        assert c.champion in raw_keys
