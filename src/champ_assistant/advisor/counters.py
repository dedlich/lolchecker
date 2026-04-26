"""Counter pick lookup against the static counter matrix.

Pure function: given an enemy + role + matrix → ordered list of counters.
"""
from __future__ import annotations

from ..data.models import CounterEntry, CounterMatrix, Role


def find_counters(
    enemy_key: str,
    role: Role,
    matrix: CounterMatrix,
    *,
    limit: int | None = None,
) -> list[CounterEntry]:
    """Return counters against ``enemy_key`` in ``role``, sorted by score desc.

    Defensively filters out the enemy itself (so even a misconfigured matrix
    can never recommend "counter Garen with Garen"). Missing keys return an
    empty list — never a KeyError.
    """
    if not enemy_key:
        return []

    raw = matrix.counters_for(enemy_key, role)
    sorted_counters = sorted(raw, key=lambda c: c.score, reverse=True)
    filtered = [c for c in sorted_counters if c.champion != enemy_key]

    if limit is not None:
        if limit < 0:
            raise ValueError(f"limit must be non-negative, got {limit}")
        filtered = filtered[:limit]

    return filtered
