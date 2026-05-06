"""Champion role inference helpers.

Extracted from ``app.py`` where these used to sit between two import
blocks (a PEP-8 violation that was harmless until the next top-level
import-order surgery would break it). They're now pure-function utilities
in the ``advisor`` package so the orchestrator + view builder can both
import them without circularity.

* ``infer_role_from_tags`` — heuristic role from Data Dragon tags
* ``role_at_index`` — fallback when LCU doesn't echo enemy assignments
"""
from __future__ import annotations

from ..data.models import Role

# Standard draft pick order — Riot fixes the role assignment to cell order
# in ranked, but the LCU only echoes assignedPosition for *my* team. We
# infer the enemy team's roles from their index within their_team as a
# last resort.
DRAFT_ROLE_ORDER: list[Role] = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]


def role_at_index(i: int) -> Role | None:
    """Return the standard draft role at slot ``i`` (0-indexed), or
    ``None`` if the index is out of range."""
    return DRAFT_ROLE_ORDER[i] if 0 <= i < len(DRAFT_ROLE_ORDER) else None


def infer_role_from_tags(tags: list[str]) -> Role | None:
    """Heuristic role guess from Data Dragon's champion tags.

    Riot's tags ({Assassin, Fighter, Mage, Marksman, Support, Tank}) are
    playstyle labels, not lanes — so the mapping is approximate.
    Hand-curated priority order based on common pick distribution; user
    can override via the role click target on EnemyRow.
    """
    s = set(tags)
    if "Marksman" in s:
        return "BOT"
    if "Support" in s:
        return "SUPPORT"
    # Pure tank without fighter chops → typically SUPPORT
    # (Leona, Naut, Alistar)
    if "Tank" in s and "Fighter" not in s:
        return "SUPPORT"
    # Tank + Fighter → top-lane bruisers (Garen, Maokai, Sett)
    if "Tank" in s and "Fighter" in s:
        return "TOP"
    # Pure mage → mid (Annie, Lux without support, Veigar)
    if "Mage" in s and "Assassin" not in s and "Fighter" not in s:
        return "MID"
    # Assassin + Fighter → jungle (Kha'Zix, Viego, Nidalee)
    if "Assassin" in s and "Fighter" in s:
        return "JUNGLE"
    # Pure assassin → mid (Zed, Talon, Akali)
    if "Assassin" in s:
        return "MID"
    # Pure fighter → top (Darius, Aatrox, Camille)
    if "Fighter" in s:
        return "TOP"
    # Mage + Assassin (LeBlanc, Diana) → mid
    if "Mage" in s:
        return "MID"
    return None
