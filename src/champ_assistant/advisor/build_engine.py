"""Build recommendation engine.

Pure Python scoring engine — no I/O, no Qt. Takes Meraki Analytics dicts
and returns ranked item recommendations for a champion archetype.

The archetype detection and scoring logic is a faithful Python port of the
lolexpert TypeScript engine (engine.ts), extended with a GameContext layer
that boosts situational items based on live LCDA data (enemy team comp,
gold deficit, game stage).

Usage
-----
::

    champion_dict = await meraki_client.fetch_champion("Ahri")
    items_dict    = await meraki_client.fetch_items()
    archetype     = detect_archetype(champion_dict)
    context       = GameContext(enemy_ap_count=3, enemy_sustain_count=1)
    result        = recommend_items(champion_dict, items_dict, archetype, context)
    # result.core_items        — top 6 scored items for the archetype
    # result.situational_items — items 7-12, adjusted for game context
"""
from __future__ import annotations

from dataclasses import dataclass


# ─── Archetype constants ──────────────────────────────────────────────────────

# Champions where Meraki's adaptiveType is wrong — they build AP despite
# being classified as PHYSICAL_DAMAGE.
_AP_OVERRIDES: frozenset[str] = frozenset({"Akali", "Corki", "Kayle", "Smolder"})

# Champions who build crit items despite melee/non-marksman roles.
_CRIT_MELEE: frozenset[str] = frozenset({"Yasuo", "Yone", "Tryndamere", "Nilah", "Master Yi"})

# Champions that cannot buy boots (Cassiopeia passive).
_NO_BOOTS: frozenset[str] = frozenset({"Cassiopeia"})

# Boot name substrings — used to filter the full item list.
_BOOT_KEYWORDS: tuple[str, ...] = (
    "berserker", "sorcerer's shoes", "plated steelcaps", "mercury's treads",
    "ionian boots", "boots of swiftness", "crimson lucidity",
    "spellslinger's shoes", "chainlaced crushers", "armored advance", "swiftmarch",
)


# ─── Public data types ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChampionArchetype:
    """Derived champion profile used by the scoring pass."""
    damage_type: str       # "physical" | "magic"
    item_damage_type: str  # same, but "physical" for tank-supports (Thresh, Nautilus)
    play_style: str        # marksman | mage | assassin | bruiser | tank | support | specialist
    is_ranged: bool
    has_mana: bool
    primary_position: str  # TOP | JUNGLE | MIDDLE | BOTTOM | SUPPORT
    scaling_attributes: frozenset[str]


@dataclass(frozen=True)
class ScoredItem:
    """One item with its archetype-fit score and human-readable reasons."""
    item_id: int
    item_name: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class GameContext:
    """Live enemy team composition used to boost situational items.

    All counts refer to the confirmed enemy team. Zero is a safe default
    when LCDA data is unavailable or the game has not started yet.
    """
    enemy_ap_count: int = 0      # AP damage dealers
    enemy_ad_count: int = 0      # AD damage dealers
    enemy_sustain_count: int = 0 # champions with heavy in-combat healing
    enemy_tank_count: int = 0    # frontline tanks (items_value + Tank tag)
    game_time_s: float = 0.0
    player_behind: bool = False  # ally gold < enemy gold by >3000


@dataclass(frozen=True)
class BuildResult:
    """Complete build recommendation for one champion in one game."""
    champion_name: str
    archetype: ChampionArchetype
    core_items: tuple[ScoredItem, ...]        # top 6 — generic archetype fit
    situational_items: tuple[ScoredItem, ...]  # items 7-12 — adjusted for game context
    boots_name: str | None
    boots_id: int | None
    starter_name: str | None
    starter_id: int | None


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _flat(stat: dict | None) -> float:
    """Extract the flat component of a Meraki stat dict."""
    if not stat:
        return 0.0
    return float(stat.get("flat") or 0)


def _pct(stat: dict | None) -> float:
    """Extract the percent component of a Meraki stat dict."""
    if not stat:
        return 0.0
    return float(stat.get("percent") or 0)


# ─── Archetype Detection ─────────────────────────────────────────────────────

def detect_archetype(champion: dict) -> ChampionArchetype:
    """Derive a champion's scoring archetype from Meraki champion data.

    The logic mirrors the TypeScript ``detectArchetype`` in lolexpert exactly,
    including the edge-case overrides for champions whose Meraki data is
    inconsistent with how they are actually built in-game.
    """
    key: str = str(champion.get("key") or champion.get("name") or "")
    roles: list[str] = list(champion.get("roles") or [])
    positions: list[str] = list(champion.get("positions") or [])
    attack_type: str = (champion.get("attackType") or "").upper()
    resource: str = (champion.get("resource") or "").upper()
    adaptive_type: str = (champion.get("adaptiveType") or "PHYSICAL_DAMAGE").upper()

    is_ranged = attack_type == "RANGED"
    has_mana = resource == "MANA"

    # Real damage type: manual overrides > Meraki adaptiveType
    has_ap_override = (
        key in _AP_OVERRIDES
        or (
            adaptive_type == "PHYSICAL_DAMAGE"
            and any(r in ("MAGE", "BURST", "ARTILLERY") for r in roles)
            # Jhin/Ezreal: MARKSMAN + MAGE tag — build physical, not AP
            and "MARKSMAN" not in roles
            and "BOTTOM" not in positions
        )
    )
    is_ap = has_ap_override or adaptive_type == "MAGIC_DAMAGE"
    damage_type = "magic" if is_ap else "physical"

    is_battlemage = "BATTLEMAGE" in roles

    # Tank-supports (Thresh, Nautilus, Blitzcrank): CATCHER/SUPPORT + TANK role
    # with MAGIC_DAMAGE adaptive. For item scoring they need tank items, not
    # enchanter items, so we flip item_damage_type to "physical".
    is_tank_support = (
        ("CATCHER" in roles or "SUPPORT" in roles) and "TANK" in roles
    )
    item_damage_type = "physical" if is_tank_support else damage_type

    # Play style — priority order matters (matches TS engine exactly)
    play_style: str
    if is_battlemage:
        play_style = "mage"
    elif key in _CRIT_MELEE:
        play_style = "marksman"
    elif "MARKSMAN" in roles and ("BOTTOM" in positions or not is_ap):
        play_style = "marksman"
    elif "ENCHANTER" in roles or "CATCHER" in roles:
        play_style = "support"
    elif any(r in ("MAGE", "BURST", "ARTILLERY") for r in roles):
        play_style = "mage"
    elif "ASSASSIN" in roles and is_ap:
        play_style = "mage"   # AP assassins (Akali, LeBlanc, Fizz)
    elif "ASSASSIN" in roles:
        play_style = "assassin"
    elif "VANGUARD" in roles:
        play_style = "tank"   # Engage frontliners (Ornn, Leona, Nautilus)
    elif any(r in ("JUGGERNAUT", "FIGHTER", "DIVER", "SKIRMISHER") for r in roles):
        play_style = "bruiser"
    elif any(r in ("TANK", "WARDEN") for r in roles):
        play_style = "tank"
    else:
        play_style = "specialist"

    # Scaling attributes — influence passive keyword bonuses below
    ratings: dict = champion.get("attributeRatings") or {}
    attrs: list[str] = []
    if float(ratings.get("damage") or 0) >= 3:
        attrs.append("damage")
    if float(ratings.get("toughness") or 0) >= 3:
        attrs.append("toughness")
    if float(ratings.get("utility") or 0) >= 3:
        attrs.append("utility")
    if float(ratings.get("mobility") or 0) >= 3:
        attrs.append("mobility")
    if float(ratings.get("abilityReliance") or 0) >= 3:
        attrs.append("abilityReliance")
    if is_battlemage:
        attrs.append("battlemage")

    # AP assassin: burst-and-escape (Akali, LeBlanc, Fizz) — not SKIRMISHER
    is_ap_assassin = (
        is_ap and play_style == "mage"
        and "ASSASSIN" in roles and not is_battlemage
        and "SKIRMISHER" not in roles
    )
    if is_ap_assassin:
        attrs.append("apAssassin")

    # AP skirmisher: sustained melee AP brawler (Sylas, Mordekaiser)
    is_ap_skirmisher = (
        is_ap and not is_battlemage and not is_ap_assassin and not is_ranged
        and (
            "SKIRMISHER" in roles
            or ("JUGGERNAUT" in roles and "MAGE" in roles)
            or ("FIGHTER" in roles and "MAGE" in roles)
        )
    )
    if is_ap_skirmisher:
        attrs.append("apSkirmisher")

    # Juggernaut: physical bruiser for Conqueror rune weighting
    is_juggernaut = play_style == "bruiser" and damage_type == "physical"
    if is_juggernaut:
        attrs.append("juggernaut")

    primary_position = (
        positions[0] if positions
        else ("SUPPORT" if play_style == "support" else "MIDDLE")
    )

    return ChampionArchetype(
        damage_type=damage_type,
        item_damage_type=item_damage_type,
        play_style=play_style,
        is_ranged=is_ranged,
        has_mana=has_mana,
        primary_position=primary_position,
        scaling_attributes=frozenset(attrs),
    )


# ─── Item Scoring ─────────────────────────────────────────────────────────────

def score_item(
    item: dict,
    archetype: ChampionArchetype,
    context: GameContext | None = None,
) -> ScoredItem:
    """Score one item for the given archetype (+ optional live game context).

    Returns a ScoredItem with score < 0 for items that should never appear
    (removed, non-purchasable, champion-restricted, components/tier-1).
    Caller must filter these out before sorting.
    """
    item_id = int(item.get("id") or 0)
    name = str(item.get("name") or "")

    # ── Hard exclusions ──────────────────────────────────────────────────
    if item.get("removed"):
        return ScoredItem(item_id, name, -999.0, ())
    shop: dict = item.get("shop") or {}
    if not shop.get("purchasable", True):
        return ScoredItem(item_id, name, -999.0, ())
    if item.get("requiredChampion"):
        return ScoredItem(item_id, name, -999.0, ())
    tier = int(item.get("tier") or 0)
    if tier < 2:
        return ScoredItem(item_id, name, -999.0, ())

    dt = archetype.item_damage_type
    ps = archetype.play_style
    is_ranged = archetype.is_ranged
    has_mana = archetype.has_mana
    attrs = archetype.scaling_attributes

    stats: dict = item.get("stats") or {}
    score = 0.0
    reasons: list[str] = []

    # ── Physical damage (not support) ─────────────────────────────────────
    if dt == "physical" and ps != "support":
        ad = _flat(stats.get("attackDamage"))
        lethality = _flat(stats.get("lethality"))
        apen = _pct(stats.get("armorPenetration"))
        crit = _pct(stats.get("criticalStrikeChance"))
        att_spd = _flat(stats.get("attackSpeed"))
        ah = _flat(stats.get("abilityHaste"))

        if ad > 0:
            score += ad * 0.8
            reasons.append(f"+{ad:.0f} Attack Damage")
        if lethality > 0:
            score += lethality * 1.5
            reasons.append(f"+{lethality:.0f} Lethality")
        if apen > 0:
            score += apen * (1.2 if ps == "marksman" else 2.0)
            reasons.append(f"+{apen:.0f}% Armor Pen")
        if crit > 0 and is_ranged and ps == "marksman":
            score += crit * 1.8
            reasons.append(f"+{crit:.0f}% Crit Chance")
        if att_spd > 0:
            score += att_spd * (1.5 if ps == "marksman" else 0.5)
            reasons.append(f"+{att_spd:.0f}% Attack Speed")
        if ah > 0 and ps in ("bruiser", "assassin"):
            score += ah * 1.5
            reasons.append(f"+{ah:.0f} Ability Haste")

    # ── Magic damage (not support) ────────────────────────────────────────
    if dt == "magic" and ps != "support":
        ap = _flat(stats.get("abilityPower"))
        mpen_pct = _pct(stats.get("magicPenetration"))
        mpen_flat = _flat(stats.get("magicPenetration"))
        ah = _flat(stats.get("abilityHaste"))
        hp = _flat(stats.get("health"))
        armor = _flat(stats.get("armor"))

        if ap > 0:
            score += ap * 0.9
            reasons.append(f"+{ap:.0f} Ability Power")
        if mpen_pct > 0:
            score += mpen_pct * 2.5
            reasons.append(f"+{mpen_pct:.0f}% Magic Pen")
        if mpen_flat > 0:
            score += mpen_flat * 3.0
            reasons.append(f"+{mpen_flat:.0f} flat Magic Pen")
        if ah > 0:
            score += ah * 2.5
            reasons.append(f"+{ah:.0f} Ability Haste")
        if armor > 0:
            score += armor * 0.4
            reasons.append(f"+{armor:.0f} Armor")

        if "apAssassin" in attrs:
            item_has_ap = _flat(stats.get("abilityPower")) > 0
            if hp > 0 and item_has_ap:
                score += hp * 0.15
                reasons.append(f"+{hp:.0f} HP (AP assassin durability)")
            ad_bonus = _flat(stats.get("attackDamage"))
            ov = _pct(stats.get("omnivamp"))
            if ad_bonus > 0 and item_has_ap:
                score += ad_bonus * 0.4
                reasons.append(f"+{ad_bonus:.0f} AD (hybrid auto scaling)")
            if ov > 0 and not is_ranged:
                score += ov * 3.0
                reasons.append(f"+{ov:.0f}% Omnivamp (melee sustain)")

        if "apSkirmisher" in attrs:
            item_has_ap_or_pen = (
                _flat(stats.get("abilityPower")) > 0
                or _pct(stats.get("magicPenetration")) > 0
                or _flat(stats.get("magicPenetration")) > 0
            )
            if hp > 0 and item_has_ap_or_pen:
                score += hp * 0.15
                reasons.append(f"+{hp:.0f} HP (AP brawler sustain)")

        if "battlemage" in attrs:
            item_has_ap_or_pen = (
                _flat(stats.get("abilityPower")) > 0
                or _pct(stats.get("magicPenetration")) > 0
                or _flat(stats.get("magicPenetration")) > 0
            )
            if hp > 0 and item_has_ap_or_pen:
                score += hp * 0.18
                reasons.append(f"+{hp:.0f} HP (Battlemage sustain)")

            pn = " ".join((p.get("name") or "").lower() for p in (item.get("passives") or []))
            il = name.lower()
            if "baleful blaze" in pn or "blackfire" in pn or "blackfire torch" in il:
                score += 80; reasons.append("% Max HP burn on ability (Blackfire Torch)")
            if "torment" in pn or "suffering" in pn or "liandry" in il:
                score += 95; reasons.append("% Max HP DoT stacks with Blackfire (Liandry's)")
            if "hatefog" in pn or "scorn" in pn or "malignance" in il:
                score += 80; reasons.append("Ultimate AH + Hatefog burn (Malignance)")
            if "rimefrost" in pn or "rylai" in il:
                score += 55; reasons.append("Slow-on-DoT keeps enemies in range (Rylai's)")
            if "sinister pact" in pn or "demonic embrace" in il:
                score += 50; reasons.append("% Max HP DoT + AP scaling passive (Demonic)")
            if "void corruption" in pn or "void infusion" in pn or "riftmaker" in il:
                score += 45; reasons.append("Stacking magic pen + sustain (Riftmaker)")
            if "void staff" in il:
                score += 15; reasons.append("40% magic pen essential vs MR-stacking enemies")

    # ── Tank / Bruiser ────────────────────────────────────────────────────
    if ps in ("tank", "bruiser"):
        hp = _flat(stats.get("health"))
        armor = _flat(stats.get("armor"))
        mr = _flat(stats.get("magicResistance"))
        ten = _pct(stats.get("tenacity"))

        hp_weight = 0.28 if ps == "tank" else 0.20
        if hp > 0:
            score += hp * hp_weight; reasons.append(f"+{hp:.0f} Health")
        if armor > 0:
            score += armor * 1.2; reasons.append(f"+{armor:.0f} Armor")
        if mr > 0:
            score += mr * 1.2; reasons.append(f"+{mr:.0f} Magic Resistance")
        if ten > 0:
            score += ten * 2.0; reasons.append(f"+{ten:.0f}% Tenacity")

    # ── Bruiser physical damage bonus ─────────────────────────────────────
    if ps == "bruiser" and dt == "physical":
        ad = _flat(stats.get("attackDamage"))
        lethality = _flat(stats.get("lethality"))
        apen = _pct(stats.get("armorPenetration"))
        ah = _flat(stats.get("abilityHaste"))
        if ad > 0:
            score += ad * 0.6; reasons.append(f"+{ad:.0f} Attack Damage")
        if lethality > 0:
            score += lethality * 1.0; reasons.append(f"+{lethality:.0f} Lethality")
        if apen > 0:
            score += apen * 1.5; reasons.append(f"+{apen:.0f}% Armor Pen")
        if ah > 0:
            score += ah * 1.5; reasons.append(f"+{ah:.0f} Ability Haste")

    # ── Support ───────────────────────────────────────────────────────────
    if ps == "support":
        hsp = _flat(stats.get("healAndShieldPower"))
        ah = _flat(stats.get("abilityHaste"))
        hp = _flat(stats.get("health"))
        ap = _flat(stats.get("abilityPower"))
        armor = _flat(stats.get("armor"))
        mr = _flat(stats.get("magicResistance"))

        if hsp > 0:
            score += hsp * 3.5; reasons.append(f"+{hsp:.0f} Heal & Shield Power")
        if ah > 0:
            score += ah * 2.5; reasons.append(f"+{ah:.0f} Ability Haste")
        hp_weight = 0.1 if dt == "magic" else 0.2
        if hp > 0:
            score += hp * hp_weight; reasons.append(f"+{hp:.0f} Health")
        if ap > 0 and dt == "magic":
            score += ap * 0.4; reasons.append(f"+{ap:.0f} AP (heal/shield scaling)")
        armor_coeff = 1.0 if dt == "physical" else 0.5
        if armor > 0:
            score += armor * armor_coeff; reasons.append(f"+{armor:.0f} Armor")
        mr_coeff = 1.0 if dt == "physical" else 0.6
        if mr > 0:
            score += mr * mr_coeff; reasons.append(f"+{mr:.0f} Magic Resistance")

    # ── Mana bonus (not support) ──────────────────────────────────────────
    if has_mana and ps != "support":
        mana = _flat(stats.get("mana"))
        mana_regen = _flat(stats.get("manaRegen"))
        mana_weight = 0.05 if dt == "magic" else 0.08
        if mana > 0:
            score += mana * mana_weight; reasons.append(f"+{mana:.0f} Mana")
        if mana_regen > 0:
            score += mana_regen * 5.0

    # ── Lifesteal / Omnivamp for bruisers ─────────────────────────────────
    if ps in ("bruiser", "fighter"):
        ls = _pct(stats.get("lifesteal"))
        ov = _pct(stats.get("omnivamp"))
        if ls > 0:
            score += ls * 2.0; reasons.append(f"+{ls:.0f}% Lifesteal")
        if ov > 0:
            score += ov * 2.0; reasons.append(f"+{ov:.0f}% Omnivamp")

    # ── Passive keyword detection ─────────────────────────────────────────
    pn = " ".join((p.get("name") or "").lower() for p in (item.get("passives") or []))
    desc = (item.get("simpleDescription") or "").lower()
    il = name.lower()

    def kw(*words: str) -> bool:
        return any(w in pn or w in il or w in desc for w in words)

    if dt == "magic" and kw("magical opus", "rabadon"):
        score += 70; reasons.append("AP amplifier +35% bonus AP (Rabadon's)")

    if dt == "magic" and kw("stasis", "zhonya"):
        bonus = (
            75 if "apAssassin" in attrs and not is_ranged
            else 70 if "battlemage" in attrs or "apSkirmisher" in attrs
            else 45
        )
        score += bonus; reasons.append("Stasis active — survive burst while diving/channeling")

    if "apAssassin" in attrs and not is_ranged and kw("gunblade"):
        score += 65; reasons.append("Hybrid AP/AD + Omnivamp für melee AP-Assassinen (Gunblade)")

    if "apAssassin" in attrs and not is_ranged and kw("rocketbelt"):
        score += 50; reasons.append("Dash-Active: gap-closer für melee AP-Assassinen (Rocketbelt)")

    if kw("cinderbloom", "shadowflame"):
        if "apAssassin" in attrs:
            score += 55; reasons.append("Execute + 15 flat MPen für Burst-Combo (Shadowflame)")
        elif "apSkirmisher" in attrs or (
            dt == "magic" and "battlemage" not in attrs and ps != "support"
        ):
            score += 45; reasons.append("Execute + 15 flat MPen (Shadowflame)")

    if "apSkirmisher" in attrs and kw("void corruption", "void infusion", "riftmaker"):
        score += 70; reasons.append("Stacking MPen + Omnivamp-Sustain im Nahkampf (Riftmaker)")

    if "apSkirmisher" in attrs and kw("glaciate", "everfrost"):
        score += 55; reasons.append("Root-Active für AP-Brawler Engage/Chain-CC (Everfrost)")

    if kw("spellblade", "lich bane") and ps != "support":
        if dt == "magic" and "battlemage" in attrs and not is_ranged:
            score += 50; reasons.append("Spellblade empowered auto (40% AP bonus)")
        elif dt == "physical" and ps in ("bruiser", "assassin"):
            score += 40; reasons.append("Spellblade empowered auto (150% base AD)")

    if ps in ("bruiser", "tank") and kw("cleave", "stridebreaker"):
        score += 40; reasons.append("AoE slow on dash (Stridebreaker)")

    if dt == "physical" and ps == "marksman" and kw("wrath", "infinity edge"):
        score += 60; reasons.append("Crit damage +40% (needs 60% crit from other items)")

    if ps == "marksman":
        if kw("wind's fury", "runaan") and is_ranged:
            score += 60; reasons.append("AoE bolt passive (Runaan's)")
        if kw("bring it down", "kraken slayer"):
            score += 55; reasons.append("% max HP on-hit vs tanks (Kraken Slayer)")
        if kw("spectral waltz", "phantom dancer"):
            score += 45; reasons.append("Ghosting + damage reduction (Phantom Dancer)")
        if kw("lifeline") and ("shieldbow" in il or "immortal" in il):
            score += 40; reasons.append("HP shield vs burst (Immortal Shieldbow)")
        if kw("transcendence", "navori", "flickerblade"):
            score += 35; reasons.append("CDR on crit (Navori Flickerblade)")
        if kw("sharpshooter", "energized"):
            score += 30; reasons.append("Energized empowered attack (Rapid Firecannon)")
        if kw("seething strike", "rageblade", "guinsoo"):
            score += 40; reasons.append("On-hit conversion (Guinsoo's Rageblade)")

    if dt == "physical" and ps in ("bruiser", "assassin") and kw("carve", "black cleaver"):
        score += 50; reasons.append("Armor shred up to 24% (Black Cleaver)")

    if dt == "physical" and ps == "assassin":
        if kw("nightfall", "duskblade"):
            score += 55; reasons.append("Stealth reset on kill (Duskblade of Draktharr)")
        if kw("wraithcaller", "youmuu", "ghostblade"):
            score += 45; reasons.append("MS burst for engage/escape (Youmuu's Ghostblade)")
        if kw("bitter cold", "serylda"):
            score += 35; reasons.append("Slow on ability for kiting (Serylda's Grudge)")

    if ps in ("tank", "bruiser") and kw("immolate", "sunfire", "hollow radiance"):
        score += 50; reasons.append("Immolate AoE damage (scales with HP)")

    if ps == "bruiser":
        if kw("the claws that catch", "sterak"):
            score += 45; reasons.append("HP shield + Tenacity (Sterak's Gage)")
        if kw("ignore pain", "death's dance"):
            score += 50; reasons.append("Delayed damage mitigation (Death's Dance)")
        if kw("voidborn resilience", "jak'sho"):
            score += 50; reasons.append("Scaling resistances in fights (Jak'Sho)")

    if ps == "tank":
        if kw("thorns", "thornmail"):
            score += 40; reasons.append("Damage reflect + Grievous Wounds (Thornmail)")
        if kw("resilience", "randuin"):
            score += 35; reasons.append("Crit damage reduction (Randuin's Omen)")
        if kw("voidborn resilience", "jak'sho"):
            score += 55; reasons.append("Scaling tank passive (Jak'Sho, The Protean)")
        if kw("colossal consumption", "heartsteel"):
            score += 50; reasons.append("Stacking HP (Heartsteel)")

    if ps == "support":
        is_enchanter = dt == "magic"
        is_tank_sup = dt == "physical"
        if kw("starlit grace", "moonstone"):
            score += 85 if is_enchanter else 30; reasons.append("Heal pulse on ability (Moonstone)")
        if kw("rapids", "flowing water"):
            score += 75 if is_enchanter else 25; reasons.append("AP + AS buff to shielded ally")
        if kw("promise", "wordless promise"):
            score += 75 if is_enchanter else 40; reasons.append("HP-scaling shield + reset passive")
        if kw("first light", "dawncore"):
            score += 70 if is_enchanter else 20; reasons.append("AP growth on heal/shield cast")
        if kw("sanctify", "ardent censer", "ardent"):
            score += 70 if is_enchanter else 20; reasons.append("Empowers carries with AS + on-hit (Ardent)")
        if kw("effervescence", "peppermint", "sword of blossoming"):
            score += 65 if is_enchanter else 20; reasons.append("Healing empowerment passive")
        if kw("redemption"):
            score += 65 if is_enchanter else 45; reasons.append("AoE team heal (Redemption)")
        if kw("locket", "iron solari"):
            score += 65; reasons.append("AoE shield active for team (Locket)")
        if kw("mikael"):
            score += 60 if is_enchanter else 40; reasons.append("CC cleanse to save carry (Mikael's)")
        if kw("harmony", "whispering circlet"):
            score += 50 if is_enchanter else 20; reasons.append("Scaling HSP from ally casts")
        if kw("sacrifice", "knight's vow"):
            score += 55; reasons.append("Damage redirect to protect ADC (Knight's Vow)")
        if kw("cryocombustion", "zeke"):
            score += 45; reasons.append("Bonus damage link to ally (Zeke's Convergence)")
        if kw("reverie", "shurelya", "fanfare", "bandlepipes"):
            score += 50; reasons.append("Team movement speed burst (Shurelya's)")
        if is_tank_sup:
            if kw("colossal consumption", "heartsteel"):
                score += 60; reasons.append("Stacking HP for tank support (Heartsteel)")
            if kw("voidborn resilience", "jak'sho"):
                score += 55; reasons.append("Scaling resistances (Jak'Sho)")
            if kw("vendetta", "anathema"):
                score += 45; reasons.append("Denying enemy carry (Anathema's Chains)")
            if kw("unmake", "abyssal"):
                score += 45; reasons.append("AP reduction aura (Abyssal Mask)")

    # ── Irrelevant-stat penalties ─────────────────────────────────────────
    has_ad = _flat(stats.get("attackDamage")) > 0
    has_leth = _flat(stats.get("lethality")) > 0
    has_crit = _pct(stats.get("criticalStrikeChance")) > 0
    has_apen = _pct(stats.get("armorPenetration")) > 0
    has_as = _flat(stats.get("attackSpeed")) > 0
    has_ap = _flat(stats.get("abilityPower")) > 0
    has_mpen_pct = _pct(stats.get("magicPenetration")) > 0
    has_mpen_flat = _flat(stats.get("magicPenetration")) > 0

    if ps not in ("support", "tank", "bruiser"):
        if dt == "physical" and not any([has_ad, has_leth, has_crit, has_apen, has_as]):
            score -= 40
        if dt == "magic" and not any([has_ap, has_mpen_pct, has_mpen_flat]):
            score -= 60

    # ── Game context adjustments (situational scoring) ────────────────────
    if context is not None and score > 0:
        mr = _flat(stats.get("magicResistance"))
        armor = _flat(stats.get("armor"))
        apen_pct = _pct(stats.get("armorPenetration"))
        mpen = _flat(stats.get("magicPenetration")) + _pct(stats.get("magicPenetration"))
        hp = _flat(stats.get("health"))

        # AP-heavy enemy comp → MR items get a strong bonus
        if context.enemy_ap_count >= 2 and mr > 0:
            bonus = context.enemy_ap_count * 8
            score += bonus
            reasons.append(f"+{bonus:.0f} vs {context.enemy_ap_count} AP-Gegner (MR)")

        # AD-heavy → armor items score higher
        if context.enemy_ad_count >= 3 and armor > 0 and ps not in ("mage",):
            bonus = context.enemy_ad_count * 6
            score += bonus
            reasons.append(f"+{bonus:.0f} vs {context.enemy_ad_count} AD-Gegner (Armor)")

        # Sustain enemies → grievous wounds items
        if context.enemy_sustain_count >= 1 and kw(
            "grievous", "morello", "executioner", "thornmail",
            "chainsword", "mortal reminder",
        ):
            bonus = 30 + context.enemy_sustain_count * 10
            score += bonus
            reasons.append(f"+{bonus:.0f} vs {context.enemy_sustain_count} Sustain-Gegner (GW)")

        # Tank-heavy enemy comp → armor/magic penetration items
        if context.enemy_tank_count >= 2 and (apen_pct > 0 or mpen > 0):
            bonus = context.enemy_tank_count * 8
            score += bonus
            reasons.append(f"+{bonus:.0f} vs {context.enemy_tank_count} Tanks (Penetration)")

        # Behind in gold → survival items get a boost
        if context.player_behind and (mr > 0 or armor > 0) and hp > 100:
            score += 15
            reasons.append("+15 Survival-Item (Golddefizit)")

    return ScoredItem(item_id, name, score, tuple(reasons))


# ─── Boots and Starter selection ─────────────────────────────────────────────

def _select_boots(archetype: ChampionArchetype, boot_items: list[dict]) -> str | None:
    def find(keyword: str) -> dict | None:
        return next(
            (b for b in boot_items if keyword in (b.get("name") or "").lower()),
            None,
        )

    if archetype.item_damage_type == "magic":
        b = find("spellslinger") or find("sorcerer") or (boot_items[0] if boot_items else None)
    elif archetype.play_style == "assassin":
        b = find("crimson lucidity") or find("ionian") or (boot_items[0] if boot_items else None)
    elif archetype.play_style == "marksman":
        b = find("berserker") or (boot_items[0] if boot_items else None)
    elif archetype.play_style == "tank":
        b = find("chainlaced") or find("mercury") or (boot_items[0] if boot_items else None)
    elif archetype.play_style == "bruiser":
        b = find("armored advance") or find("plated") or (boot_items[0] if boot_items else None)
    elif archetype.play_style == "support":
        b = (
            find("chainlaced") or find("mercury")
            or find("armored") or (boot_items[0] if boot_items else None)
        )
    else:
        b = boot_items[0] if boot_items else None

    return str(b.get("name")) if b else None


def _select_starter(archetype: ChampionArchetype, all_items: list[dict]) -> str | None:
    starter_items = [
        item for item in all_items
        if int(item.get("tier") or 0) == 1
        and not item.get("removed")
        and (item.get("shop") or {}).get("purchasable", True)
        and ((item.get("shop") or {}).get("prices") or {}).get("total", 0) <= 500
        and (item.get("name") or "").lower().startswith("doran")
    ]

    def find(keyword: str) -> dict | None:
        return next(
            (i for i in starter_items if keyword in (i.get("name") or "").lower()),
            None,
        )

    ps = archetype.play_style
    dt = archetype.item_damage_type

    if ps == "marksman":
        item = find("bow") or find("blade")
    elif ps in ("tank", "bruiser"):
        item = find("helm") or find("shield")
    elif dt == "physical" and ps != "support":
        item = find("blade")
    elif dt == "magic":
        item = find("ring")
    else:
        item = None

    return str(item.get("name")) if item else None


# ─── Main recommendation function ────────────────────────────────────────────

def recommend_items(
    champion: dict,
    items: dict,
    archetype: ChampionArchetype,
    context: GameContext | None = None,
) -> BuildResult:
    """Score all items for this archetype and return a BuildResult.

    ``items`` is the raw Meraki items dict (keyed by item-id string).
    ``context`` boosts situational items based on live game state.

    The top-6 scored items become core; items 7-12 become situational.
    Boots and starter Doran's item are selected separately.
    """
    all_items = list(items.values())

    # Only score completed Summoner's Rift items (ranked 5v5).
    # tier=1: starters/components/trinkets — excluded.
    # tier=2: intermediate components and boots — excluded here; boots are
    #         selected separately via _BOOT_KEYWORDS on all_items below.
    # tier=3: completed legendary items — the only ones we recommend.
    # tier=4: transform items (Muramana, Seraph's) — purchasable=False, excluded.
    # id < 10000: ARAM augments (220000+) and special items already excluded.
    completed = [
        item for item in all_items
        if int(item.get("id") or 0) < 10_000
        and int(item.get("tier") or 0) >= 3
        and not item.get("removed")
        and (item.get("shop") or {}).get("purchasable", True)
        and not item.get("requiredChampion")
    ]

    scored = [score_item(item, archetype, context) for item in completed]
    scored = sorted(
        (s for s in scored if s.score > 0),
        key=lambda s: s.score,
        reverse=True,
    )

    # Build a name→id lookup from the Meraki items dict for boots/starter ID resolution.
    _name_to_id: dict[str, int] = {}
    for _raw_item in all_items:
        _n = _raw_item.get("name") or ""
        _id_str = str(_raw_item.get("id") or 0)
        if _n and _id_str.isdigit():
            _name_to_id[_n] = int(_id_str)

    # Boots — matched by name substring from the full item list (not just
    # completed tier-2 items, because boots straddle tiers by patch).
    champion_key = str(champion.get("key") or "")
    boots_name: str | None = None
    boots_id: int | None = None
    if champion_key not in _NO_BOOTS:
        boot_items = [
            item for item in all_items
            if not item.get("removed")
            and (item.get("shop") or {}).get("purchasable", True)
            and ((item.get("shop") or {}).get("prices") or {}).get("total", 0) >= 900
            and any(kw in (item.get("name") or "").lower() for kw in _BOOT_KEYWORDS)
        ]
        boots_name = _select_boots(archetype, boot_items)
        if boots_name:
            boots_id = _name_to_id.get(boots_name)

    starter_name = _select_starter(archetype, all_items)
    starter_id = _name_to_id.get(starter_name) if starter_name else None

    # Split into core (top 6) and situational (items 7-12), deduplicating by name.
    seen: set[str] = set()
    core: list[ScoredItem] = []
    situational: list[ScoredItem] = []
    for s in scored:
        if s.item_name in seen:
            continue
        seen.add(s.item_name)
        if len(core) < 6:
            core.append(s)
        elif len(situational) < 6:
            situational.append(s)
        if len(core) >= 6 and len(situational) >= 6:
            break

    return BuildResult(
        champion_name=str(champion.get("name") or ""),
        archetype=archetype,
        core_items=tuple(core),
        situational_items=tuple(situational),
        boots_name=boots_name,
        boots_id=boots_id,
        starter_name=starter_name,
        starter_id=starter_id,
    )
