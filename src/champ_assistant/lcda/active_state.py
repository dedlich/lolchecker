"""Active player combat state — HP %, mana %, current gold.

LCDA's ``activePlayer`` block exposes more than what ``power_spikes``
needs (level + items). Pulling HP/mana/gold separately enables the
B5 recommendation rules (recall windows, low-HP safety) without
touching the existing power-spike call site.

Field shapes
============
LCDA's ``championStats`` field uses these keys (verified across
patch 14.x):
  * ``currentHealth`` / ``maxHealth``
  * ``resourceValue`` / ``resourceMax``  (mana / energy / fury / heat)
  * ``resourceType``                       ("MANA", "ENERGY", "RAGE", ...)

Energy users (Akali / Kennen / Lee Sin / Shen / Zed) regenerate fast
enough that "mana low" is rarely actionable. We mark them as
non-mana users so the rule short-circuits the mana-poke check for
them.

Defensive parsing
-----------------
LCDA returns numbers as floats but field types differ across game
patches; we coerce to float and clamp to [0, 1] so a buggy frame
(currentHealth > maxHealth right after a heal-buff drop) can't push
hp_pct above 1.0 into rule misfires.
"""
from __future__ import annotations

from dataclasses import dataclass

# Resource types LCDA reports. Only "MANA" gets the depletion warning;
# others either regenerate fast or aren't gated on resource at all.
_MANA_USING_RESOURCES: frozenset[str] = frozenset({"MANA", "BLOOD"})


@dataclass(frozen=True)
class ActiveCombatState:
    """One-tick view of the local player's combat resources.

    All percentages are in [0, 1] — easier to compare against thresholds
    than raw HP values which scale wildly between champions and patches.
    ``gold`` is absolute (LCDA's ``currentGold``) since item prices are
    absolute too.

    ``unspent_skill_points`` = (player level − sum of Q/W/E/R levels).
    Pros never carry an unspent point past the kill that gave it; solo-
    queue regularly does. Surfaced as a low-severity nag when above 0.
    """
    gold: float = 0.0
    hp_pct: float = 1.0      # 1.0 = full health; 0.0 = dead
    mana_pct: float = 1.0    # 1.0 = full mana; 1.0 for non-mana users
    is_mana_user: bool = False
    resource_type: str = ""  # raw LCDA value, kept for diagnostics
    unspent_skill_points: int = 0


def _coerce_float(raw: object, default: float = 0.0) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    return default


def _safe_pct(current: float, maximum: float) -> float:
    """Compute ``current / maximum`` clamped to [0, 1]. Returns 1.0 when
    ``maximum`` is 0 (avoids div-by-zero and treats "unknown" as full)."""
    if maximum <= 0.0:
        return 1.0
    pct = current / maximum
    if pct < 0.0:
        return 0.0
    if pct > 1.0:
        return 1.0
    return pct


def _count_unspent_skill_points(active_player: dict) -> int:
    """``player.level − sum(Q/W/E/R levels)``, clamped at 0.

    LCDA's ``abilities`` block uses ``abilityLevel`` (newer patches) or
    ``level`` (older); both are tried defensively. Missing or malformed
    fields degrade to 0 unspent — better to under-fire than flood a
    pre-patched client with bogus warnings.
    """
    level = int(active_player.get("level") or 0)
    if level <= 0:
        return 0
    abilities = active_player.get("abilities") or {}
    if not isinstance(abilities, dict):
        return 0
    spent = 0
    for slot in ("Q", "W", "E", "R"):
        slot_data = abilities.get(slot)
        if not isinstance(slot_data, dict):
            continue
        # Try both field names — Riot has shipped both across patches.
        raw = slot_data.get("abilityLevel")
        if raw is None:
            raw = slot_data.get("level")
        try:
            spent += int(raw or 0)
        except (TypeError, ValueError):
            pass
    return max(0, level - spent)


def extract_active_combat_state(active_player: dict | None) -> ActiveCombatState:
    """Pull HP %, mana %, gold, and unspent-skill-point count from LCDA's
    ``activePlayer`` block.

    Returns the default (all-full, no-data) state when input is None or
    missing the expected fields. Defensive across patch differences —
    every key access uses ``.get(...)`` with a sensible fallback.
    """
    if not active_player or not isinstance(active_player, dict):
        return ActiveCombatState()

    gold = _coerce_float(active_player.get("currentGold"), 0.0)
    unspent = _count_unspent_skill_points(active_player)
    stats = active_player.get("championStats") or {}
    if not isinstance(stats, dict):
        return ActiveCombatState(gold=gold, unspent_skill_points=unspent)

    hp_cur = _coerce_float(stats.get("currentHealth"))
    hp_max = _coerce_float(stats.get("maxHealth"))
    hp_pct = _safe_pct(hp_cur, hp_max)

    res_cur = _coerce_float(stats.get("resourceValue"))
    res_max = _coerce_float(stats.get("resourceMax"))
    resource_type = str(stats.get("resourceType") or "").upper()
    is_mana_user = resource_type in _MANA_USING_RESOURCES
    # Non-mana users get mana_pct=1.0 so any "low mana" rule short-circuits.
    mana_pct = _safe_pct(res_cur, res_max) if is_mana_user else 1.0

    return ActiveCombatState(
        gold=gold,
        hp_pct=hp_pct,
        mana_pct=mana_pct,
        is_mana_user=is_mana_user,
        resource_type=resource_type,
        unspent_skill_points=unspent,
    )
