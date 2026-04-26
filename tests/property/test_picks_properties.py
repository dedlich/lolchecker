"""Property-based tests for suggest_picks."""
from __future__ import annotations

from hypothesis import given, strategies as st

from champ_assistant.advisor.composition import analyze_composition
from champ_assistant.advisor.picks import suggest_picks
from champ_assistant.data.models import CounterMatrix, TagsData, TierList

from .conftest import champion_keys, counter_matrix, roles, tags_data, tier_list


@given(
    role=roles,
    my_team=st.lists(champion_keys, max_size=5),
    enemy=st.lists(champion_keys, max_size=5),
    tiers=tier_list(),
    counters=counter_matrix(),
    tags=tags_data(),
    limit=st.integers(min_value=0, max_value=10),
)
def test_score_in_range(
    role: str,
    my_team: list[str],
    enemy: list[str],
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
    limit: int,
) -> None:
    """Every score must fall in [0, 100]."""
    gaps = analyze_composition(my_team, tags)
    suggestions = suggest_picks(role, my_team, enemy, gaps, tiers, counters, tags, limit=limit)  # type: ignore[arg-type]
    for s in suggestions:
        assert 0.0 <= s.score <= 100.0


@given(
    role=roles,
    my_team=st.lists(champion_keys, max_size=5),
    enemy=st.lists(champion_keys, max_size=5),
    tiers=tier_list(),
    counters=counter_matrix(),
    tags=tags_data(),
)
def test_sorted_by_score_descending(
    role: str,
    my_team: list[str],
    enemy: list[str],
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
) -> None:
    gaps = analyze_composition(my_team, tags)
    suggestions = suggest_picks(role, my_team, enemy, gaps, tiers, counters, tags)  # type: ignore[arg-type]
    scores = [s.score for s in suggestions]
    assert scores == sorted(scores, reverse=True)


@given(
    role=roles,
    my_team=st.lists(champion_keys, max_size=5, unique=True),
    enemy=st.lists(champion_keys, max_size=5, unique=True),
    tiers=tier_list(),
    counters=counter_matrix(),
    tags=tags_data(),
)
def test_never_suggests_drafted_champion(
    role: str,
    my_team: list[str],
    enemy: list[str],
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
) -> None:
    """No suggestion may match a champion already on either team."""
    gaps = analyze_composition(my_team, tags)
    suggestions = suggest_picks(role, my_team, enemy, gaps, tiers, counters, tags)  # type: ignore[arg-type]
    drafted = set(my_team) | set(enemy)
    for s in suggestions:
        assert s.champion_key not in drafted


@given(
    role=roles,
    my_team=st.lists(champion_keys, max_size=5),
    enemy=st.lists(champion_keys, max_size=5),
    tiers=tier_list(),
    counters=counter_matrix(),
    tags=tags_data(),
    limit=st.integers(min_value=0, max_value=20),
)
def test_respects_limit(
    role: str,
    my_team: list[str],
    enemy: list[str],
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
    limit: int,
) -> None:
    gaps = analyze_composition(my_team, tags)
    suggestions = suggest_picks(role, my_team, enemy, gaps, tiers, counters, tags, limit=limit)  # type: ignore[arg-type]
    assert len(suggestions) <= limit


@given(
    role=roles,
    tiers=tier_list(),
    counters=counter_matrix(),
    tags=tags_data(),
)
def test_suggestion_keys_come_from_tier_list(
    role: str,
    tiers: TierList,
    counters: CounterMatrix,
    tags: TagsData,
) -> None:
    """Every suggested champion must originate from the tier list for that role."""
    gaps = analyze_composition([], tags)
    suggestions = suggest_picks(role, [], [], gaps, tiers, counters, tags)  # type: ignore[arg-type]
    role_keys = {e.champion for e in tiers.tiers.get(role, [])}  # type: ignore[arg-type]
    for s in suggestions:
        assert s.champion_key in role_keys
