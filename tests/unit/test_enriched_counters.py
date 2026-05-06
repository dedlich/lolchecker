"""Unit tests for view_builder._enriched_counters.

The function merges the static seed CounterMatrix with cached Lolalytics
data from RuntimeCounterStore so the fallback pick-suggestion path
benefits from previously fetched data without firing new network
requests.

(Previously tested via ``ChampAssistant._enriched_counters``; the
helper moved to ``view_builder`` in v1.10.54 — these tests were
rewritten to call it directly through the view-builder's deps.)
"""
from __future__ import annotations

from unittest.mock import MagicMock

from champ_assistant.data.models import (
    BuildLibrary,
    CounterEntry,
    CounterMatrix,
    TagsData,
    TierList,
)
from champ_assistant.view_builder import ViewBuilderDeps, _enriched_counters


def _deps(
    *,
    seed_matrix: dict | None = None,
    runtime_counters: object | None = None,
) -> ViewBuilderDeps:
    """Build a minimal ViewBuilderDeps for testing _enriched_counters.
    Most fields are placeholders — only ``counters`` and
    ``runtime_counters`` are read by the helper under test."""
    return ViewBuilderDeps(
        connection_state="disconnected",
        counters=CounterMatrix(matrix=seed_matrix or {}),
        tiers=TierList(),
        tags=TagsData(),
        champions={},
        builds=BuildLibrary(),
        runtime_counters=runtime_counters,  # type: ignore[arg-type]
        enemy_role_overrides={},
        enemy_profiles_by_cell={},
        ally_profiles_by_cell={},
        schedule_runtime_fetch=lambda *_: None,
    )


def _store(cache: dict[tuple[str, str], list[CounterEntry] | None]) -> MagicMock:
    """Build a fake RuntimeCounterStore whose get_cached returns from cache."""
    store = MagicMock()
    store.get_cached = lambda key, role: cache.get((key, role))
    return store


# -------------------------------------------------------------------------
# No runtime store — returns seed matrix unchanged
# -------------------------------------------------------------------------

def test_returns_seed_matrix_when_no_runtime_store() -> None:
    seed = {"Yasuo": {"TOP": [CounterEntry(champion="Darius", score=8.0)]}}
    deps = _deps(seed_matrix=seed, runtime_counters=None)
    result = _enriched_counters(["Yasuo"], "TOP", deps)
    assert result is deps.counters


# -------------------------------------------------------------------------
# No cached data for any enemy — returns seed matrix unchanged (identity)
# -------------------------------------------------------------------------

def test_returns_seed_matrix_when_cache_empty() -> None:
    seed = {"Yasuo": {"TOP": [CounterEntry(champion="Darius", score=8.0)]}}
    store = _store({})  # no cached entries
    deps = _deps(seed_matrix=seed, runtime_counters=store)
    result = _enriched_counters(["Garen"], "TOP", deps)
    assert result is deps.counters


# -------------------------------------------------------------------------
# Cached data for one enemy — merged into result
# -------------------------------------------------------------------------

def test_merges_cached_data_for_known_enemy() -> None:
    seed = {}
    fresh = [CounterEntry(champion="Fiora", score=9.0)]
    store = _store({("Darius", "TOP"): fresh})
    deps = _deps(seed_matrix=seed, runtime_counters=store)

    result = _enriched_counters(["Darius"], "TOP", deps)
    assert result is not deps.counters
    entries = result.matrix["Darius"]["TOP"]
    assert any(e.champion == "Fiora" for e in entries)


# -------------------------------------------------------------------------
# Cached data overwrites seed entry for the same enemy+role
# -------------------------------------------------------------------------

def test_cached_data_overwrites_seed_entry() -> None:
    seed_entries = [CounterEntry(champion="Garen", score=5.0)]
    seed = {"Yasuo": {"TOP": seed_entries}}
    fresh = [CounterEntry(champion="Malphite", score=9.5)]
    store = _store({("Yasuo", "TOP"): fresh})
    deps = _deps(seed_matrix=seed, runtime_counters=store)

    result = _enriched_counters(["Yasuo"], "TOP", deps)
    entries = result.matrix["Yasuo"]["TOP"]
    assert any(e.champion == "Malphite" for e in entries)
    assert not any(e.champion == "Garen" for e in entries)


# -------------------------------------------------------------------------
# Multiple enemies — only the ones with cached data are enriched
# -------------------------------------------------------------------------

def test_only_enemies_with_cache_are_enriched() -> None:
    fresh_darius = [CounterEntry(champion="Fiora", score=9.0)]
    store = _store({("Darius", "TOP"): fresh_darius})
    deps = _deps(runtime_counters=store)

    result = _enriched_counters(["Darius", "Garen"], "TOP", deps)
    # Darius enriched
    assert "Darius" in result.matrix
    # Garen has no cache → not added
    assert "Garen" not in result.matrix


# -------------------------------------------------------------------------
# Seed entries for other enemies/roles preserved after enrichment
# -------------------------------------------------------------------------

def test_seed_entries_preserved_after_enrichment() -> None:
    seed = {"Yasuo": {"MID": [CounterEntry(champion="Malphite", score=7.0)]}}
    fresh = [CounterEntry(champion="Fiora", score=9.0)]
    store = _store({("Darius", "TOP"): fresh})
    deps = _deps(seed_matrix=seed, runtime_counters=store)

    result = _enriched_counters(["Darius"], "TOP", deps)
    # Original Yasuo MID entry untouched
    assert "Yasuo" in result.matrix
    mid_entries = result.matrix["Yasuo"]["MID"]
    assert any(e.champion == "Malphite" for e in mid_entries)


# -------------------------------------------------------------------------
# Empty enemy list → seed returned unchanged
# -------------------------------------------------------------------------

def test_empty_enemy_keys_returns_seed() -> None:
    store = _store({})
    deps = _deps(runtime_counters=store)
    result = _enriched_counters([], "TOP", deps)
    assert result is deps.counters
