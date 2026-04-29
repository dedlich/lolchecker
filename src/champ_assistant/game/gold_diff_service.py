"""Gold-difference computation — team totals + best-effort lane breakdown.

Spec contract
=============
Output shape::

    {
      "team_blue":  int,                 # signed, blue-side perspective
      "team_red":   int,                 # always == -team_blue (mirror)
      "lane_breakdown": dict[str, int],  # 5 lanes from blue perspective,
                                         # OR empty {} if inference fails
    }

Lane inference is BEST-EFFORT and fails closed. LCDA does not expose
positions; we reconstruct lane assignments from:

  * Smite presence (the Smite-holder is the jungler)
  * Champion role tags (DataDragon tags — passed in by the caller)

If any team can't be fully classified (multiple smites, missing
champion data, ambiguous remaining roles), ``lane_breakdown`` is
returned as ``{}`` and the UI falls back to team-only display.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..lcda.players import LivePlayer
    from ..lcda.source import LcdaSnapshot


class GoldDiff(TypedDict):
    team_blue: int
    team_red: int
    lane_breakdown: dict[str, int]


# Lane order is canonical — UI iterates this list to render rows.
LANE_ORDER = ("top", "jungle", "mid", "adc", "support")

# Riot's "ORDER" team plays from bottom-left = blue side.
BLUE_TEAM = "ORDER"
RED_TEAM = "CHAOS"

# Champion role tags that map to a likely lane. Tags from DataDragon
# are shared across multiple lanes (a champ tagged "Marksman, Mage"
# could be ADC or mid-mage depending on patch); we score per-lane
# affinity rather than hard-classify. Higher number = stronger pick
# probability for that lane on the current SR meta.
TAG_LANE_AFFINITY: dict[str, dict[str, float]] = {
    "Tank":     {"top": 1.0, "support": 0.8, "jungle": 0.4},
    "Fighter":  {"top": 1.0, "jungle": 0.5},
    "Marksman": {"adc": 1.0, "mid": 0.1},
    "Mage":     {"mid": 1.0, "support": 0.5},
    "Assassin": {"mid": 0.8, "jungle": 0.7, "top": 0.3},
    "Support":  {"support": 1.0},
}


def _has_smite(player: "LivePlayer") -> bool:
    """Smite is the canonical jungler signal — exactly one per team."""
    return any(
        spell.name.lower() == "smite"
        for spell in (player.spell_one, player.spell_two)
    )


def _classify_team(
    players: list["LivePlayer"],
    champion_tags: dict[str, list[str]],
) -> dict[str, "LivePlayer"] | None:
    """Map ``{lane: player}`` for one team. Returns ``None`` on any
    ambiguity — caller falls back to team-only display."""
    if len(players) != 5:
        return None

    # Step 1: Smite-holder is the jungler. Reject if 0 or >1 smites
    # (ARAM, draft mistake, or LCDA glitch).
    junglers = [p for p in players if _has_smite(p)]
    if len(junglers) != 1:
        return None
    jungler = junglers[0]
    remaining = [p for p in players if p is not jungler]

    # Step 2: score each remaining player's affinity for each lane.
    # Index-based — using LivePlayer as a dict key would require
    # callers to pass hashable types; we don't need that constraint.
    scores: list[dict[str, float]] = []
    for player in remaining:
        tags = champion_tags.get(player.champion_name, [])
        if not tags:
            return None  # missing champion data — fail closed
        score: dict[str, float] = {lane: 0.0 for lane in LANE_ORDER if lane != "jungle"}
        for tag in tags:
            for lane, weight in TAG_LANE_AFFINITY.get(tag, {}).items():
                if lane == "jungle":
                    continue
                score[lane] = score.get(lane, 0.0) + weight
        scores.append(score)

    # Greedy assignment: for each lane in order of "specificity"
    # (support first since the Support tag is unambiguous, ADC
    # second, then mid, then top), pick the highest-scoring
    # remaining player.
    assignment: dict[str, "LivePlayer"] = {"jungle": jungler}
    pool_indices = list(range(len(remaining)))
    for lane in ("support", "adc", "mid", "top"):
        if not pool_indices:
            return None
        best_idx = max(pool_indices, key=lambda i: scores[i].get(lane, 0.0))
        if scores[best_idx].get(lane, 0.0) <= 0.0:
            return None  # nobody has affinity for this lane → ambiguous
        assignment[lane] = remaining[best_idx]
        pool_indices.remove(best_idx)

    # Sanity: every lane is filled.
    if any(lane not in assignment for lane in LANE_ORDER):
        return None
    return assignment


def _team_for_side(snapshot: "LcdaSnapshot", side_label: str) -> list["LivePlayer"]:
    """Return players on ``side_label`` (ORDER or CHAOS) regardless of
    which side the active player is on."""
    return [
        p for p in (list(snapshot.allies) + list(snapshot.enemies))
        if p.team == side_label
    ]


def compute_team_gold_diff(
    snapshot: "LcdaSnapshot | None",
    *,
    champion_tags: dict[str, list[str]] | None = None,
) -> GoldDiff:
    """Compute team gold diff + best-effort lane breakdown.

    The function is named ``compute_team_gold_diff`` to preserve the
    import signature shipped in the previous commit; the return shape
    has changed and consumers must read ``team_blue`` / ``team_red``
    / ``lane_breakdown`` rather than the old ``team`` key.

    Defensive on every input — None snapshot, missing aggregates, bad
    types all collapse to a zero-diff dict with empty lane_breakdown.
    The UI never has to defend against missing keys.
    """
    empty: GoldDiff = {"team_blue": 0, "team_red": 0, "lane_breakdown": {}}
    if snapshot is None:
        return empty

    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    if ally is None or enemy is None:
        return empty

    ally_value = getattr(ally, "items_value", 0)
    enemy_value = getattr(enemy, "items_value", 0)
    if not isinstance(ally_value, (int, float)) or not isinstance(enemy_value, (int, float)):
        return empty

    # Active player's team / enemy team labels — used to map ally/enemy
    # totals to blue/red. Falls back to ally=blue / enemy=red when
    # active_team is missing (degenerate but doesn't crash).
    active_team = getattr(snapshot, "active_team", "") or BLUE_TEAM
    enemy_team = getattr(snapshot, "enemy_team", "") or (
        RED_TEAM if active_team == BLUE_TEAM else BLUE_TEAM
    )

    if active_team == BLUE_TEAM:
        blue_value, red_value = int(ally_value), int(enemy_value)
    else:
        blue_value, red_value = int(enemy_value), int(ally_value)

    blue_diff = blue_value - red_value

    # Lane breakdown — best-effort, only when champion_tags provided.
    lane_breakdown = _try_lane_breakdown(snapshot, champion_tags) if champion_tags else {}

    return {
        "team_blue": blue_diff,
        "team_red": -blue_diff,
        "lane_breakdown": lane_breakdown,
    }


def _try_lane_breakdown(
    snapshot: "LcdaSnapshot",
    champion_tags: dict[str, list[str]],
) -> dict[str, int]:
    """Best-effort per-lane gold delta from blue's perspective.
    Returns ``{}`` if either side can't be fully classified."""
    blue_players = _team_for_side(snapshot, BLUE_TEAM)
    red_players = _team_for_side(snapshot, RED_TEAM)

    blue_lanes = _classify_team(blue_players, champion_tags)
    red_lanes = _classify_team(red_players, champion_tags)
    if blue_lanes is None or red_lanes is None:
        return {}

    breakdown: dict[str, int] = {}
    for lane in LANE_ORDER:
        b = blue_lanes.get(lane)
        r = red_lanes.get(lane)
        if b is None or r is None:
            return {}
        b_value = getattr(b, "items_value", 0)
        r_value = getattr(r, "items_value", 0)
        if not isinstance(b_value, (int, float)) or not isinstance(r_value, (int, float)):
            return {}
        breakdown[lane] = int(b_value) - int(r_value)
    return breakdown
