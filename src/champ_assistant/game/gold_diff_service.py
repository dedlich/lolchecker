"""Team-level gold-difference computation.

Pure function over LCDA's TeamAggregate values. NO per-lane breakdown
— LCDA doesn't expose reliable lane assignment, so any per-lane
number we synthesized would be wrong often enough to mislead.

Spec contract
=============
* Output is ``int`` (not float). Items_value in LCDA is already an
  integer sum of per-player items_value, but we explicitly round any
  float-precision artifacts away so the displayed number is stable.
* Positive = our team ahead, negative = enemy ahead, zero = equal.
* Deterministic — same input → same output, no time-dependent or
  random elements.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..lcda.source import LcdaSnapshot


class GoldDiff(TypedDict):
    """Team-only gold diff payload.

    Per-lane fields were considered + dropped — see module docstring.
    Keep the dict shape minimal so a future per-lane addition is a
    pure extension (no field removals)."""
    team: int


def compute_team_gold_diff(snapshot: "LcdaSnapshot | None") -> GoldDiff:
    """Return ``{"team": int}`` from an LCDA snapshot.

    Returns ``{"team": 0}`` when:
      * snapshot is None (no in-game data)
      * either team aggregate is missing (game just started, items
        not yet computed)

    Both fall-back paths are valid game states, not error conditions
    — the UI displays 0 in those cases.
    """
    if snapshot is None:
        return {"team": 0}

    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    if ally is None or enemy is None:
        return {"team": 0}

    ally_value = getattr(ally, "items_value", 0)
    enemy_value = getattr(enemy, "items_value", 0)
    if not isinstance(ally_value, (int, float)) or not isinstance(enemy_value, (int, float)):
        return {"team": 0}

    return {"team": int(ally_value) - int(enemy_value)}
