"""Shared hypothesis strategies for advisor property tests."""
from __future__ import annotations

from hypothesis import strategies as st

from champ_assistant.data.models import (
    CounterEntry,
    CounterMatrix,
    TagsData,
    TierEntry,
    TierList,
)

ROLES = ("TOP", "JUNGLE", "MID", "BOT", "SUPPORT")
TAG_VOCAB = (
    "Tank", "Bruiser", "Engage", "Diver",
    "Mage", "Burst", "Wave-Clear",
    "Marksman", "Fighter", "Assassin",
    "Enchanter", "Peel", "Crowd-Control",
)
TIERS = ("S+", "S", "A", "B", "C", "D")

champion_keys = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    min_size=2,
    max_size=12,
)
roles = st.sampled_from(ROLES)
tiers = st.sampled_from(TIERS)
scores = st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)


@st.composite
def counter_matrix(draw: st.DrawFn) -> CounterMatrix:
    enemies = draw(st.lists(champion_keys, max_size=8, unique=True))
    matrix: dict[str, dict[str, list[CounterEntry]]] = {}
    for enemy in enemies:
        per_role: dict[str, list[CounterEntry]] = {}
        for role in draw(st.lists(roles, max_size=5, unique=True)):
            entries = draw(
                st.lists(st.tuples(champion_keys, scores), max_size=8)
            )
            per_role[role] = [CounterEntry(champion=c, score=s) for c, s in entries]
        matrix[enemy] = per_role
    return CounterMatrix(matrix=matrix)  # type: ignore[arg-type]


@st.composite
def tier_list(draw: st.DrawFn) -> TierList:
    data: dict[str, list[TierEntry]] = {}
    for role in draw(st.lists(roles, max_size=5, unique=True)):
        entries = draw(
            st.lists(st.tuples(champion_keys, tiers), max_size=10, unique_by=lambda x: x[0])
        )
        data[role] = [TierEntry(champion=c, tier=t) for c, t in entries]
    return TierList(tiers=data)  # type: ignore[arg-type]


@st.composite
def tags_data(draw: st.DrawFn) -> TagsData:
    keys = draw(st.lists(champion_keys, max_size=10, unique=True))
    tags = {
        k: draw(st.lists(st.sampled_from(TAG_VOCAB), max_size=4, unique=True))
        for k in keys
    }
    return TagsData(tags=tags)
