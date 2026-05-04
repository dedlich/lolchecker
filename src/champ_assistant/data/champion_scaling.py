"""Extract per-champion ability damage-scaling ratios from Meraki champion data.

Meraki stores leveling modifiers as:
  abilities.{P/Q/W/E/R}[0].effects[].leveling[].modifiers[].{values, units}

where units[0] is a string like "% AP", "% AD", "% bonus AD".
We sum the level-1 coefficient (values[0]) for each scaling type across all
damaging abilities to produce a ChampionScalingProfile.

This profile drives two improvements in the build engine:
  1. Hybrid item scoring — on hybrid champions (e.g. Akali, Corki, Kayle),
     items that provide AD get a proportional bonus score even though the
     archetype is classified as "magic" damage.
  2. Reduced "no-AP" penalty — an AD item on a hybrid champion is not
     irrelevant; its penalty is reduced from −60 to −20.

Example values for Akali (Q 65%AD + 60%AP, E 100%AD + 110%AP, R 50%AD + 90%AP):
  ap_ratio    ≈ 290
  ad_ratio    ≈ 215
  is_hybrid   = True
  primary     = "ap"
"""
from __future__ import annotations

from dataclasses import dataclass


# Attribute text substrings that indicate a damage-type leveling row.
# Deliberately excludes movement speed, range, slow duration, stack counts, etc.
_DAMAGE_KW = ("damage", "execute", "wound", "burn", "bleed", "detonation")
_HEAL_KW   = ("heal", "shield", "restore", "regenerat")


def _is_damage(attr: str) -> bool:
    low = attr.lower()
    return any(kw in low for kw in _DAMAGE_KW)


def _is_heal(attr: str) -> bool:
    low = attr.lower()
    return any(kw in low for kw in _HEAL_KW)


@dataclass(frozen=True)
class ChampionScalingProfile:
    """AP / AD ability ratios derived from Meraki champion spell data.

    All ratios are cumulative sums at rank 1 across all abilities — they are
    relative weights for item scoring, not absolute ability percentages.
    """
    champion_key: str
    ap_ratio: float        # sum of % AP coefficients across all damaging abilities
    ad_ratio: float        # sum of % total-AD coefficients
    bonus_ad_ratio: float  # sum of % bonus-AD coefficients
    heal_ap_ratio: float   # sum of % AP from heals / shields (e.g. Sylas W)
    is_hybrid: bool        # True when both AP and physical ratios are ≥ 30
    primary_scaling: str   # "ap" | "ad" | "hybrid"


def extract_scaling_profile(champion: dict) -> ChampionScalingProfile:
    """Parse a Meraki champion dict and return its ability scaling ratios.

    Gracefully handles missing or malformed data — never raises.
    """
    key = str(champion.get("key") or champion.get("name") or "")
    ap_total       = 0.0
    ad_total       = 0.0
    bonus_ad_total = 0.0
    heal_ap_total  = 0.0

    for _slot, ability_list in (champion.get("abilities") or {}).items():
        if not ability_list:
            continue
        ability: dict = ability_list[0]  # base form (first of potentially multiple)
        for effect in (ability.get("effects") or []):
            for leveling in (effect.get("leveling") or []):
                attr    = str(leveling.get("attribute") or "")
                is_dmg  = _is_damage(attr)
                is_heal = _is_heal(attr)
                if not is_dmg and not is_heal:
                    continue

                for mod in (leveling.get("modifiers") or []):
                    units  = mod.get("units")  or []
                    values = mod.get("values") or []
                    if not units or not values:
                        continue
                    unit = str(units[0]).lower().strip()
                    try:
                        val = float(values[0])
                    except (TypeError, ValueError):
                        continue

                    if "% ap" in unit or "ability power" in unit:
                        if is_dmg:
                            ap_total += val
                        else:
                            heal_ap_total += val
                    elif "% bonus ad" in unit or "% bonus attack" in unit:
                        if is_dmg:
                            bonus_ad_total += val
                    elif "% ad" in unit or "% attack damage" in unit or "% total ad" in unit:
                        if is_dmg:
                            ad_total += val

    total_phys = ad_total + bonus_ad_total
    is_hybrid  = ap_total >= 30.0 and total_phys >= 30.0

    if ap_total >= total_phys * 1.3:
        primary = "ap"
    elif total_phys > ap_total * 1.3:
        primary = "ad"
    else:
        primary = "hybrid"

    return ChampionScalingProfile(
        champion_key=key,
        ap_ratio=ap_total,
        ad_ratio=ad_total,
        bonus_ad_ratio=bonus_ad_total,
        heal_ap_ratio=heal_ap_total,
        is_hybrid=is_hybrid,
        primary_scaling=primary,
    )
