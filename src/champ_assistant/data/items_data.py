"""Riot item-id mapping for the items our seed builds reference.

LCU's ``/lol-item-sets/v1/item-sets/{summonerId}/sets`` endpoint expects
items keyed by Riot's integer IDs. Our ``builds.json`` stores item names
for human readability. The IDs below come straight from Data Dragon's
``items.json`` and are stable across patches (Riot only ever adds new
items; existing IDs don't move).

Names not in this table are silently dropped from the generated item
set so Apply Build still works for partial coverage — League itself
shows the gaps next to the keystone slots that we did fill.
"""
from __future__ import annotations

# Item-name → integer-id from Data Dragon's items.json
# Boots, legendaries, and a few support/jungle items — every name our
# builds.json actually references plus a couple of common alternates.
ITEM_IDS: dict[str, int] = {
    # ---- Boots ----------------------------------------------------------
    "Berserker's Greaves":         3006,
    "Boots of Swiftness":          3009,
    "Ionian Boots of Lucidity":    3158,
    "Mercury's Treads":            3111,
    "Mobility Boots":              3117,
    "Plated Steelcaps":            3047,
    "Sorcerer's Shoes":            3020,
    "Chainlaced Crushers":         3173,   # MR + Tenacity boots
    "Armored Advance":             3174,   # Armor boots
    # ---- AD / Crit / Attack Speed ---------------------------------------
    "Black Cleaver":               3071,
    "Blade of the Ruined King":    3153,
    "Blade of The Ruined King":    3153,   # DataDragon capitalisation variant
    "Bloodthirster":               3072,
    "Death's Dance":               6333,
    "Eclipse":                     6692,
    "Edge of Night":               3814,
    "Essence Reaver":              3508,
    "Experimental Hexplate":       3073,
    "Guardian Angel":              3026,
    "Hullbreaker":                 3181,
    "Immortal Shieldbow":          6673,
    "Infinity Edge":               3031,
    "Kraken Slayer":               6672,
    "Lord Dominik's Regards":      3036,
    "Manamune":                    3004,
    "Mercurial Scimitar":          3139,
    "Mortal Reminder":             3033,
    "Muramana":                    3042,
    "Navori Flickerblade":         6675,
    "Navori Quickblades":          6675,   # old name kept for existing builds.json
    "Phantom Dancer":              3046,
    "Profane Hydra":               6698,
    "Rapid Firecannon":            3094,
    "Ravenous Hydra":              3074,
    "Runaan's Hurricane":          3085,
    "Spear of Shojin":             3161,
    "Statikk Shiv":                3087,
    "Sterak's Gage":               3053,
    "Stormrazor":                  3097,
    "Stridebreaker":               6631,
    "Sundered Sky":                6610,
    "The Collector":               6676,
    "Terminus":                    3302,
    "Titanic Hydra":               3748,
    "Trinity Force":               3078,
    "Umbral Glaive":               3179,
    "Wit's End":                   3091,
    "Youmuu's Ghostblade":         3142,
    "Yun Tal Wildarrows":          3032,
    # ---- Bruiser / Hybrid AD --------------------------------------------
    "Bastionbreaker":              2520,
    "Chempunk Chainsword":         6609,
    "Endless Hunger":              2517,
    "Guinsoo's Rageblade":         3124,
    "Hextech Gunblade":            3146,
    "Maw of Malmortius":           3156,
    "Overlord's Bloodmail":        2501,
    "Serylda's Grudge":            6694,
    "Voltaic Cyclosword":          6699,
    # ---- Assassin / Lethality ------------------------------------------
    "Axiom Arc":                   6696,
    "Hubris":                      6697,
    "Serpent's Fang":              6695,
    # ---- AP items -------------------------------------------------------
    "Archangel's Staff":           3003,
    "Blackfire Torch":             2503,
    "Bloodletter's Curse":         8010,
    "Cosmic Drive":                4629,
    "Cryptbloom":                  3137,
    "Dawncore":                    6621,
    "Dusk and Dawn":               2510,
    "Hextech Rocketbelt":          3152,
    "Horizon Focus":               4628,
    "Liandry's Torment":           6653,
    "Liandry's Anguish":           6653,   # old name kept for existing builds.json
    "Lich Bane":                   3100,
    "Luden's Echo":                6655,
    "Luden's Companion":           6655,   # old name kept for existing builds.json
    "Ludens Companion":            6655,   # alt spelling sometimes used
    "Malignance":                  4633,
    "Morellonomicon":              3165,
    "Nashor's Tooth":              3115,
    "Rabadon's Deathcap":          3089,
    "Riftmaker":                   4633,
    "Rod of Ages":                 6657,
    "Rylai's Crystal Scepter":     3116,
    "Shadowflame":                 4645,
    "Stormsurge":                  4646,
    "Void Staff":                  3135,
    "Zhonya's Hourglass":          3157,
    "Actualizer":                  2522,
    "Hexoptics C44":               2523,
    # ---- Tank items -----------------------------------------------------
    "Abyssal Mask":                8020,
    "Bandlepipes":                 2524,
    "Dead Man's Plate":            3742,
    "Force of Nature":             4401,
    "Frozen Heart":                3110,
    "Heartsteel":                  7028,
    "Hollow Radiance":             6664,
    "Iceborn Gauntlet":            6662,
    "Jak'Sho, The Protean":        6665,
    "Kaenic Rookern":              2504,
    "Protoplasm Harness":          2525,
    "Randuin's Omen":              3143,
    "Spirit Visage":               3065,
    "Sunfire Aegis":               3068,
    "Thornmail":                   3075,
    "Unending Despair":            2502,
    "Warmog's Armor":              3083,
    # ---- Support / Enchanter --------------------------------------------
    "Ardent Censer":               3504,
    "Banshee's Veil":              3102,
    "Bloodsong":                   3877,   # support quest item
    "Echoes of Helia":             6620,
    "Imperial Mandate":            4005,
    "Knight's Vow":                3109,
    "Locket of the Iron Solari":   3190,
    "Mikael's Blessing":           3222,
    "Moonstone Renewer":           6617,
    "Redemption":                  3107,
    "Shurelya's Battlesong":       2065,
    "Staff of Flowing Water":      6616,
    "Zeke's Convergence":          3050,
    # ---- On-hit / special -----------------------------------------------
    "Fiendhunter Bolts":           2512,
    "Stasis Enchant":              3175,   # jungle + support passive item
    "Winter's Approach":           3119,
    "Mejai's Soulstealer":         3041,
    "Hexdrinker":                  3155,   # common component → Maw
    "Flesheater":                  667112,
}


# A reasonable starter row for every champion type — used when builds.json
# doesn't specify but we still want to ship a useful "Starting" block.
DEFAULT_STARTERS: dict[str, list[int]] = {
    "ad": [1055, 2003, 3340],   # Doran's Blade + Health Potions + Trinket
    "ap": [1056, 2003, 3340],   # Doran's Ring
    "tank": [1054, 2003, 3340], # Doran's Shield
    "support": [3850, 2003, 3340],  # Spellthief's Edge
    "jungle": [1041, 2003, 3340],   # Hailblade
}


def item_ids_for(item_names: list[str]) -> list[int]:
    """Resolve as many item names as possible into integer IDs.
    Unknown names are silently skipped — League shows the gap."""
    return [ITEM_IDS[n] for n in item_names if n in ITEM_IDS]
