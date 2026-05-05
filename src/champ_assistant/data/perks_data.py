"""Riot perk-id mapping for the runes our seed builds reference.

Riot's LCU expects perk pages keyed by integer IDs. Our ``builds.json``
stores rune *names* for human readability, so we need a translation
table on the way out. The IDs below come straight from
``https://ddragon.leagueoflegends.com/cdn/<patch>/data/en_US/runesReforged.json``
and are stable across patches (Riot only ever adds new perks; existing
IDs don't move).
"""
from __future__ import annotations

# The five runic styles ("trees"). Each keystone belongs to exactly one.
STYLE_PRECISION   = 8000
STYLE_DOMINATION  = 8100
STYLE_SORCERY     = 8200
STYLE_RESOLVE     = 8400
STYLE_INSPIRATION = 8300


# Map every keystone to its style ID. Used to derive primary_style from
# the seed-build's keystone choice.
KEYSTONE_TO_STYLE: dict[str, int] = {
    # Precision
    "Press the Attack":    STYLE_PRECISION,
    "Lethal Tempo":        STYLE_PRECISION,
    "Fleet Footwork":      STYLE_PRECISION,
    "Conqueror":           STYLE_PRECISION,
    # Domination
    "Electrocute":         STYLE_DOMINATION,
    "Predator":            STYLE_DOMINATION,
    "Dark Harvest":        STYLE_DOMINATION,
    "Hail of Blades":      STYLE_DOMINATION,
    # Sorcery
    "Summon Aery":         STYLE_SORCERY,
    "Arcane Comet":        STYLE_SORCERY,
    "Phase Rush":          STYLE_SORCERY,
    # Resolve
    "Grasp of the Undying": STYLE_RESOLVE,
    "Aftershock":          STYLE_RESOLVE,
    "Guardian":             STYLE_RESOLVE,
    # Inspiration
    "Glacial Augment":     STYLE_INSPIRATION,
    "Unsealed Spellbook":  STYLE_INSPIRATION,
    "First Strike":        STYLE_INSPIRATION,
}


# Perk-name → perk-id. Covers every name referenced in our builds.json
# library plus a few common shards. Names not in this table are skipped
# so the resulting page applies the keystone + as many runes as we can
# resolve, leaving the rest empty for the user to fill in League.
PERK_IDS: dict[str, int] = {
    # ---- Precision keystones + tree
    "Press the Attack": 8005,
    "Lethal Tempo":     8008,
    "Fleet Footwork":   8021,
    "Conqueror":        8010,
    "Overheal":            9101,
    "Triumph":             9111,
    "Presence of Mind":    8009,
    "Legend: Alacrity":    9104,
    "Legend: Tenacity":    9105,
    "Legend: Bloodline":   9103,
    "Coup de Grace":       8014,
    "Cut Down":            8017,
    "Last Stand":          8299,
    # ---- Domination keystones + tree
    "Electrocute":      8112,
    "Predator":         8124,
    "Dark Harvest":     8128,
    "Hail of Blades":   9923,
    "Cheap Shot":          8126,
    "Taste of Blood":      8139,
    "Sudden Impact":       8143,
    # Row 3 (Riot rebuilt — old: Zombie Ward / Ghost Poro / Eyeball Collection)
    "Sixth Sense":         8137,
    "Grisly Mementos":     8140,
    "Deep Ward":           8141,
    "Treasure Hunter":     8135,
    "Relentless Hunter":   8105,
    "Ultimate Hunter":     8106,
    # ---- Sorcery keystones + tree
    "Summon Aery":      8214,
    "Arcane Comet":     8229,
    "Phase Rush":       8230,
    "Nullifying Orb":      8224,
    "Manaflow Band":       8226,
    "Nimbus Cloak":        8275,
    "Transcendence":       8210,
    "Celerity":            8234,
    "Absolute Focus":      8233,
    "Scorch":              8237,
    "Waterwalking":        8232,
    "Gathering Storm":     8236,
    # ---- Resolve keystones + tree
    "Grasp of the Undying": 8437,
    "Aftershock":           8439,
    "Guardian":             8465,
    "Demolish":             8446,
    "Font of Life":         8463,
    "Shield Bash":          8401,
    "Conditioning":         8429,
    "Second Wind":          8444,
    "Bone Plating":         8473,
    "Overgrowth":           8451,
    "Revitalize":           8453,
    "Unflinching":          8242,
    # ---- Inspiration keystones + tree
    "Glacial Augment":      8351,
    "Unsealed Spellbook":   8360,
    "First Strike":         8369,
    "Hextech Flashtraption": 8306,
    "Magical Footwear":     8304,
    "Cash Back":            8321,
    "Triple Tonic":         8313,
    "Time Warp Tonic":      8352,
    "Biscuit Delivery":     8345,
    "Cosmic Insight":       8347,
    "Approach Velocity":    8410,
    "Jack of All Trades":   8316,
    # ---- Stat shards (common defaults; exact picks vary).
    "Adaptive Force":       5008,
    "Attack Speed":         5005,
    "Ability Haste":        5007,
    "Health":               5001,
    "Tenacity and Slow Resist": 5013,
    "Move Speed":           5010,
    "Health Scaling":       5011,
    "Armor":                5002,
    "Magic Resist":         5003,
}


def resolve_keystone(rune_names: list[str]) -> tuple[str, int] | None:
    """Find the first rune in the list that's a known keystone.
    Returns ``(name, style_id)`` or ``None`` if no keystone matched."""
    for name in rune_names:
        style = KEYSTONE_TO_STYLE.get(name)
        if style is not None:
            return name, style
    return None


def perk_ids_for(rune_names: list[str]) -> list[int]:
    """Translate as many rune names as possible into integer IDs.
    Unknown names are silently skipped — League fills the gaps."""
    return [PERK_IDS[n] for n in rune_names if n in PERK_IDS]
