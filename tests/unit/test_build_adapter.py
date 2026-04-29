"""Tests for matchup-aware build adaptation heuristics."""
from __future__ import annotations

from champ_assistant.advisor.build_adapter import (
    HARD_CC_KEYS,
    SUSTAIN_KEYS,
    adapt_build,
    damage_profile_for_tags,
)
from champ_assistant.data.models import ChampionBuild, TagsData


def _tags(mapping: dict[str, list[str]]) -> TagsData:
    return TagsData(tags=mapping)


def _ad_build() -> ChampionBuild:
    return ChampionBuild(
        runes=["Conqueror", "Triumph", "Legend: Alacrity", "Last Stand"],
        items=["Stridebreaker", "Sterak's Gage", "Death's Dance", "Plated Steelcaps"],
        summoners=["Flash", "Teleport"],
    )


def _ap_build() -> ChampionBuild:
    return ChampionBuild(
        runes=["Electrocute", "Sudden Impact", "Eyeball Collection", "Ultimate Hunter"],
        items=["Ludens Companion", "Shadowflame", "Rabadon's Deathcap", "Sorcerer's Shoes"],
        summoners=["Flash", "Ignite"],
    )


# ----------------------------------------------------------------------
# Boots adaptation — AP-heavy vs AD-heavy enemy comp
# ----------------------------------------------------------------------
def test_ap_heavy_team_swaps_to_mercury_treads() -> None:
    """3+ Mages on enemy team → swap any boots for Mercury's Treads."""
    base = _ad_build()  # has Plated Steelcaps
    tags = _tags({
        "Ahri":   ["Mage", "Assassin"],
        "Ryze":   ["Mage", "Battlemage"],
        "Annie":  ["Mage", "Burst"],
        "Garen":  ["Fighter", "Tank"],
        "Thresh": ["Catcher", "Mage"],   # Catcher = mage-tag too
    })
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Ahri", "Ryze", "Annie", "Garen", "Thresh"],
        tags=tags,
    )
    assert result is not None
    assert "Mercury's Treads" in result.build.items
    assert "Plated Steelcaps" not in result.build.items
    assert any("AP-heavy" in r for r in result.reasons)


def test_ad_heavy_team_swaps_to_plated_steelcaps() -> None:
    """3+ AD champs → swap to Plated Steelcaps (only if base wasn't already)."""
    base = _ap_build()  # has Sorcerer's Shoes
    tags = _tags({
        "Garen":   ["Fighter", "Tank"],
        "Vayne":   ["Marksman"],
        "LeeSin":  ["Fighter", "Skirmisher"],
        "Talon":   ["Assassin"],
        "Ashe":    ["Marksman"],
    })
    result = adapt_build(
        base, role="MID",
        enemy_team_keys=["Garen", "Vayne", "LeeSin", "Talon", "Ashe"],
        tags=tags,
    )
    assert result is not None
    assert "Plated Steelcaps" in result.build.items
    assert "Sorcerer's Shoes" not in result.build.items


def test_mixed_team_does_not_swap_boots() -> None:
    """2 AP / 2 AD / 1 unclassified → no clear dominance, no swap."""
    base = _ad_build()
    tags = _tags({
        "Ahri":   ["Mage"],
        "Annie":  ["Mage"],
        "Garen":  ["Fighter", "Tank"],
        "Vayne":  ["Marksman"],
        "Thresh": ["Catcher"],
    })
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Ahri", "Annie", "Garen", "Vayne", "Thresh"],
        tags=tags,
    )
    assert result is not None
    # Plated Steelcaps survives — no AP/AD dominance.
    assert "Plated Steelcaps" in result.build.items


# ----------------------------------------------------------------------
# Anti-heal item against sustain champions
# ----------------------------------------------------------------------
def test_sustain_in_team_triggers_antiheal() -> None:
    """Aatrox on enemy → swap last non-boots item to anti-heal.
    Boots (Plated Steelcaps) must survive — they're a separate slot
    semantically."""
    base = _ad_build()  # last item is Plated Steelcaps (boots)
    tags = _tags({"Aatrox": ["Fighter", "Bruiser"]})
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Aatrox"],
        tags=tags,
    )
    assert result is not None
    assert "Mortal Reminder" in result.build.items
    # Boots survived
    assert "Plated Steelcaps" in result.build.items
    assert any("sustain" in r.lower() for r in result.reasons)


def test_sustain_with_ap_build_uses_morellonomicon() -> None:
    base = _ap_build()  # last item is Sorcerer's Shoes (boots)
    tags = _tags({"Aatrox": ["Fighter", "Bruiser"]})
    result = adapt_build(
        base, role="MID",
        enemy_team_keys=["Aatrox"],
        tags=tags,
    )
    assert result is not None
    assert "Morellonomicon" in result.build.items
    # Boots survived
    assert "Sorcerer's Shoes" in result.build.items


def test_no_sustain_no_antiheal() -> None:
    base = _ad_build()
    tags = _tags({"Garen": ["Fighter"]})
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Garen"],
        tags=tags,
    )
    assert result is not None
    # Last item unchanged (still Plated Steelcaps).
    assert result.build.items[-1] == "Plated Steelcaps"


# ----------------------------------------------------------------------
# Tenacity rune against heavy CC
# ----------------------------------------------------------------------
def test_heavy_cc_swaps_legend_to_tenacity() -> None:
    """2+ hard-CC champs → Legend: Alacrity → Legend: Tenacity."""
    base = _ad_build()  # has Legend: Alacrity
    tags = _tags({})
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Leona", "Nautilus"],  # both in HARD_CC_KEYS
        tags=tags,
    )
    assert result is not None
    assert "Legend: Tenacity" in result.build.runes
    assert "Legend: Alacrity" not in result.build.runes
    assert any("CC" in r for r in result.reasons)


def test_one_hard_cc_does_not_trigger_tenacity() -> None:
    base = _ad_build()
    tags = _tags({})
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Leona"],
        tags=tags,
    )
    assert result is not None
    assert "Legend: Alacrity" in result.build.runes


# ----------------------------------------------------------------------
# Composition: multiple rules can fire on the same matchup
# ----------------------------------------------------------------------
def test_compounding_adaptations() -> None:
    """AP-heavy + sustain + heavy CC → all three swaps fire."""
    base = _ad_build()
    tags = _tags({
        "Vladimir":  ["Mage"],
        "Annie":     ["Mage"],
        "Lissandra": ["Mage"],
        "Leona":     ["Tank"],
        "Aatrox":    ["Fighter"],
    })
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Vladimir", "Annie", "Lissandra", "Leona", "Aatrox"],
        tags=tags,
    )
    assert result is not None
    # Mercury's Treads from AP-heavy
    assert "Mercury's Treads" in result.build.items
    # Anti-heal from sustain (Vladimir + Aatrox)
    assert any(it in ("Morellonomicon", "Mortal Reminder") for it in result.build.items)
    # Tenacity rune from CC (Leona + Lissandra both hard CC)
    assert "Legend: Tenacity" in result.build.runes
    assert len(result.reasons) >= 3


# ----------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------
def test_none_base_returns_none() -> None:
    result = adapt_build(
        None, role="TOP", enemy_team_keys=["Ahri"], tags=_tags({}),
    )
    assert result is None


def test_no_enemy_picks_yet_returns_unmodified() -> None:
    """Early champ-select before enemies pick — adapter should
    return base unchanged with empty reasons."""
    base = _ad_build()
    result = adapt_build(
        base, role="TOP", enemy_team_keys=[], tags=_tags({}),
    )
    assert result is not None
    assert result.reasons == []
    assert result.build.items == base.items


def test_empty_string_keys_filtered() -> None:
    """Locked + un-locked enemies — empty strings shouldn't trip
    the heuristic."""
    base = _ad_build()
    tags = _tags({"Ahri": ["Mage"], "Annie": ["Mage"]})
    result = adapt_build(
        base, role="TOP",
        enemy_team_keys=["Ahri", "Annie", "", "", ""],
        tags=tags,
    )
    assert result is not None
    # Only 2 valid AP picks — below the 3-AP threshold, no boot swap.
    assert "Plated Steelcaps" in result.build.items


# ----------------------------------------------------------------------
# Constants / config sanity
# ----------------------------------------------------------------------
def test_sustain_keys_includes_canonical_examples() -> None:
    for key in ("Aatrox", "Vladimir", "Yone", "Soraka"):
        assert key in SUSTAIN_KEYS


def test_hard_cc_keys_includes_canonical_engagers() -> None:
    for key in ("Leona", "Nautilus", "Blitzcrank", "Thresh"):
        assert key in HARD_CC_KEYS


# ----------------------------------------------------------------------
# damage_profile_for_tags — drives the per-enemy AP/AD badge
# ----------------------------------------------------------------------
def test_damage_profile_pure_ap() -> None:
    assert damage_profile_for_tags(["Mage", "Burst"]) == "AP"


def test_damage_profile_pure_ad() -> None:
    assert damage_profile_for_tags(["Marksman"]) == "AD"
    assert damage_profile_for_tags(["Fighter", "Tank"]) == "AD"


def test_damage_profile_hybrid() -> None:
    """Akali / Kayle: tagged as both Mage AND Assassin, so the player
    needs to know the enemy can deal both damage types."""
    assert damage_profile_for_tags(["Mage", "Assassin"]) == "AP/AD"


def test_damage_profile_pure_tank_returns_empty() -> None:
    """Pure-Tank champions (e.g. Sion, Malphite without Mage tag) deal
    minor damage and don't push MR vs Armor decisions one way or
    another — the badge should be hidden, not misleading."""
    assert damage_profile_for_tags(["Tank"]) == ""


def test_damage_profile_enchanter_returns_empty() -> None:
    """Enchanters (Soraka, Lulu, Janna): no AP/AD tag → badge hidden."""
    assert damage_profile_for_tags([]) == ""
