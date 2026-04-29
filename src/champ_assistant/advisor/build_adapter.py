"""Matchup-aware build adaptation.

The base build for ``(champion_key, role)`` is role-generic — same Yasuo
mid build regardless of what's on the enemy team. This adapter applies
deterministic heuristics on top of the base build to surface the build
that fits the SPECIFIC matchup:

  * Enemy team is AP-heavy           → Mercury's Treads
  * Enemy team is AD-heavy           → Plated Steelcaps
  * Enemy has heavy sustain (≥1)     → Grievous Wounds item replacement
  * Enemy has heavy hard CC (≥2)     → Legend: Tenacity rune

Each adaptation produces a short human-readable reason ("vs AP-heavy:
Mercury's Treads") so the UI can show what was changed and why. No
adaptation is applied silently — the user always sees what's been
swapped.

Honest scope
============
This is a heuristic layer, not a per-matchup curated DB. It improves
the obvious cases (boots, anti-heal, tenacity) where standard League
wisdom is unambiguous. Champion-specific matchup builds ("Yasuo vs
Zed: rush Maw of Malmortius") would need a curated dataset that
doesn't exist in this codebase. Out of scope for now.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..data.models import ChampionBuild, TagsData


# Tags that mark a champion as primarily AP damage.
AP_TAGS = frozenset({"Mage", "Burst", "Battlemage", "Catcher", "Specialist"})
# Tags that mark a champion as primarily AD damage.
AD_TAGS = frozenset({"Marksman", "Fighter", "Assassin", "Bruiser", "Skirmisher", "Diver", "Hyper-Carry"})


def damage_profile_for_tags(tags: list[str] | tuple[str, ...] | set[str]) -> str:
    """Classify a single champion's primary damage type from their
    DataDragon tags. Returns one of:

      ``"AP"``     — has any AP-leaning tag, no AD-leaning tag
      ``"AD"``     — has any AD-leaning tag, no AP-leaning tag
      ``"AP/AD"``  — both (hybrid: Akali, Kayle, etc.)
      ``""``       — no recognized damage tag (Tank-only, Enchanter)

    Used by the EnemyRow badge so the player can decide MR vs Armor
    item priority at a glance, and by the team-comp build adapter
    via ``_team_damage_profile``.
    """
    s = set(tags)
    has_ap = bool(s & AP_TAGS)
    has_ad = bool(s & AD_TAGS)
    if has_ap and has_ad:
        return "AP/AD"
    if has_ap:
        return "AP"
    if has_ad:
        return "AD"
    return ""
# Champions known for heavy in-combat sustain — anti-heal items
# (Morellonomicon / Executioner's Calling) are matchup wins against
# them. Curated list — DataDragon doesn't tag "self-sustain".
SUSTAIN_KEYS = frozenset({
    "Aatrox", "Vladimir", "Yone", "Soraka", "Sion", "Soraka",
    "Olaf", "Ramus", "Warwick", "Volibear", "Mundo", "Dr. Mundo",
    "DrMundo", "Kayn", "Sett", "Illaoi", "Trundle", "Swain",
    "Renekton", "Aphelios", "Senna", "Kindred", "Nilah",
})
# Champions known for hard, lockdown CC (stuns / suppressions /
# unbounded knockups). Counts toward the "heavy CC team" rule that
# flips Glacial Augment-style runes / boots.
HARD_CC_KEYS = frozenset({
    "Leona", "Nautilus", "Blitzcrank", "Thresh", "Pyke",
    "Annie", "Veigar", "Lissandra", "Malzahar", "Morgana",
    "Ashe", "Sejuani", "Amumu", "Maokai", "Cassiopeia",
    "Skarner", "Warwick", "MissFortune",
})

# Boots map: which generic-boot in a base build gets swapped for
# which adaptation. Items NOT in this map don't swap.
_GENERIC_BOOTS = {
    "Plated Steelcaps", "Mercury's Treads", "Berserker's Greaves",
    "Sorcerer's Shoes", "Ionian Boots of Lucidity",
    "Mobility Boots", "Boots of Swiftness",
}


@dataclass(frozen=True)
class AdaptedBuild:
    """Result of build adaptation: the new build + the human-readable
    reasons for whatever was changed. ``reasons`` is empty when no
    adaptation applied."""
    build: "ChampionBuild"
    reasons: list[str] = field(default_factory=list)


def _team_damage_profile(
    enemy_keys: list[str], tags: "TagsData",
) -> tuple[int, int]:
    """Return (ap_count, ad_count) over the enemy team based on tag
    overlap. A champion with both AP and AD tags counts toward both
    (e.g. Akali) — the heuristic only fires when one side is clearly
    dominant."""
    ap = ad = 0
    for key in enemy_keys:
        if not key:
            continue
        champ_tags = set(tags.tags_for(key))
        if champ_tags & AP_TAGS:
            ap += 1
        if champ_tags & AD_TAGS:
            ad += 1
    return ap, ad


def _swap_item(items: list[str], replacement: str) -> tuple[list[str], str | None]:
    """Replace the first generic-boot item with ``replacement``.
    Returns (new_list, swapped_out_or_None). No-op if no boots found
    or if the replacement is already present."""
    if replacement in items:
        return list(items), None
    out = list(items)
    for i, item in enumerate(out):
        if item in _GENERIC_BOOTS:
            old = out[i]
            out[i] = replacement
            return out, old
    return out, None


def _swap_last_item(
    items: list[str], replacement: str,
) -> tuple[list[str], str | None]:
    """Replace the latest non-boots item slot with ``replacement``.
    Anti-heal is always a late-game flex item — it must NOT overwrite
    the boots slot, which is its own conceptual category. Walks from
    the end and picks the first slot whose current item isn't in the
    generic-boots set. No-op if replacement is already present or
    every slot is boots."""
    if replacement in items:
        return list(items), None
    if not items:
        return list(items), None
    out = list(items)
    for i in range(len(out) - 1, -1, -1):
        if out[i] in _GENERIC_BOOTS:
            continue
        old = out[i]
        out[i] = replacement
        return out, old
    # Pathological: every slot is boots. Don't swap.
    return out, None


def _swap_rune(
    runes: list[str], target: str, replacement: str,
) -> tuple[list[str], bool]:
    """Replace ``target`` rune with ``replacement`` if found. Returns
    (new_list, did_swap)."""
    if target not in runes or replacement in runes:
        return list(runes), False
    out = [replacement if r == target else r for r in runes]
    return out, True


def adapt_build(
    base: "ChampionBuild | None",
    *,
    role: str,
    enemy_team_keys: list[str],
    tags: "TagsData",
) -> AdaptedBuild | None:
    """Apply matchup-aware adjustments. Returns ``None`` when ``base``
    is None (no build available) — caller falls back to no build. Otherwise
    returns ``AdaptedBuild`` with the (possibly-modified) build + reasons.
    """
    if base is None:
        return None

    from ..data.models import ChampionBuild

    items = list(base.items)
    runes = list(base.runes)
    summoners = list(base.summoners)
    reasons: list[str] = []

    enemy_keys = [k for k in enemy_team_keys if k]
    if not enemy_keys:
        # Early champ-select before enemy picks are visible — no
        # adaptation possible. Return base unchanged.
        return AdaptedBuild(build=base, reasons=[])

    ap, ad = _team_damage_profile(enemy_keys, tags)

    # Rule 1 — boots swap based on dominant enemy damage type.
    # Only swap when the dominance is clear (≥3 of one side AND that
    # side is at least 2 ahead of the other). Mixed comps stay
    # un-adapted to avoid bad swaps on borderline cases.
    if ap >= 3 and ap - ad >= 2:
        items, old = _swap_item(items, "Mercury's Treads")
        if old and old != "Mercury's Treads":
            reasons.append(f"vs AP-heavy ({ap} AP): {old} → Mercury's Treads")
    elif ad >= 3 and ad - ap >= 2:
        items, old = _swap_item(items, "Plated Steelcaps")
        if old and old != "Plated Steelcaps":
            reasons.append(f"vs AD-heavy ({ad} AD): {old} → Plated Steelcaps")

    # Rule 2 — anti-heal item against sustain champions.
    sustain_count = sum(1 for k in enemy_keys if k in SUSTAIN_KEYS)
    if sustain_count >= 1:
        # Pick the right anti-heal flavor based on champion damage type.
        # Mages → Morellonomicon, AD → Executioner's Calling/Mortal Reminder.
        # We don't know our own champion's damage type from the base
        # build alone — best heuristic: if existing items contain any
        # AP-marker item, use Morellonomicon, else AD anti-heal.
        ap_markers = {
            "Rabadon's Deathcap", "Sorcerer's Shoes", "Liandry's Anguish",
            "Ludens Companion", "Lich Bane", "Shadowflame",
            "Hextech Rocketbelt", "Nashor's Tooth",
        }
        is_ap_build = any(it in ap_markers for it in items)
        anti_heal = "Morellonomicon" if is_ap_build else "Mortal Reminder"
        items, old = _swap_last_item(items, anti_heal)
        if old and old != anti_heal:
            sustain_names = ", ".join(
                k for k in enemy_keys if k in SUSTAIN_KEYS
            )
            reasons.append(
                f"vs sustain ({sustain_names}): {old} → {anti_heal}"
            )

    # Rule 3 — Tenacity rune against heavy hard-CC enemy team.
    cc_count = sum(1 for k in enemy_keys if k in HARD_CC_KEYS)
    if cc_count >= 2:
        # The Legend rune slot is in the precision tree; swap whichever
        # Legend variant the base uses to Tenacity if it isn't already.
        for variant in ("Legend: Alacrity", "Legend: Bloodline"):
            new_runes, swapped = _swap_rune(
                runes, variant, "Legend: Tenacity",
            )
            if swapped:
                runes = new_runes
                reasons.append(
                    f"vs heavy CC ({cc_count} hard-CC): "
                    f"{variant} → Legend: Tenacity"
                )
                break

    if not reasons:
        return AdaptedBuild(build=base, reasons=[])
    adapted = ChampionBuild(
        runes=runes, items=items, summoners=summoners,
    )
    return AdaptedBuild(build=adapted, reasons=reasons)
