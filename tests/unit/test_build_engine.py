"""Tests for the Meraki-based build recommendation engine."""
from __future__ import annotations

import pytest

from champ_assistant.advisor.build_engine import (
    BuildResult,
    ChampionArchetype,
    GameContext,
    ScoredItem,
    detect_archetype,
    recommend_items,
    score_item,
)


# ─── Champion fixture builders ────────────────────────────────────────────────

def _champ(
    *,
    key: str = "TestChamp",
    roles: list[str] | None = None,
    positions: list[str] | None = None,
    attack_type: str = "MELEE",
    resource: str = "MANA",
    adaptive_type: str = "PHYSICAL_DAMAGE",
    attribute_ratings: dict | None = None,
) -> dict:
    return {
        "key": key,
        "name": key,
        "roles": roles or [],
        "positions": positions or ["MIDDLE"],
        "attackType": attack_type,
        "resource": resource,
        "adaptiveType": adaptive_type,
        "attributeRatings": attribute_ratings or {},
    }


def _mage_champ(**kw) -> dict:
    return _champ(
        roles=["MAGE", "BURST"],
        positions=["MIDDLE"],
        attack_type="RANGED",
        resource="MANA",
        adaptive_type="MAGIC_DAMAGE",
        **kw,
    )


def _marksman_champ(**kw) -> dict:
    return _champ(
        roles=["MARKSMAN"],
        positions=["BOTTOM"],
        attack_type="RANGED",
        resource="MANA",
        adaptive_type="PHYSICAL_DAMAGE",
        **kw,
    )


def _bruiser_champ(**kw) -> dict:
    return _champ(
        roles=["FIGHTER", "JUGGERNAUT"],
        positions=["TOP"],
        attack_type="MELEE",
        resource="MANA",
        adaptive_type="PHYSICAL_DAMAGE",
        **kw,
    )


# ─── Item fixture builders ────────────────────────────────────────────────────

_NEXT_ID = 1001


def _item(
    *,
    name: str = "TestItem",
    item_id: int | None = None,
    tier: int = 2,
    removed: bool = False,
    purchasable: bool = True,
    price: int = 2600,
    required_champion: str = "",
    stats: dict | None = None,
    passives: list[dict] | None = None,
) -> dict:
    global _NEXT_ID
    if item_id is None:
        _NEXT_ID += 1
        item_id = _NEXT_ID
    return {
        "id": item_id,
        "name": name,
        "tier": tier,
        "removed": removed,
        "requiredChampion": required_champion,
        "shop": {"purchasable": purchasable, "prices": {"total": price}},
        "stats": stats or {},
        "passives": passives or [],
    }


def _ap_item(name: str = "AP Item", ap: float = 100.0, **kw) -> dict:
    return _item(name=name, stats={"abilityPower": {"flat": ap}}, **kw)


def _ad_item(name: str = "AD Item", ad: float = 60.0, **kw) -> dict:
    return _item(name=name, stats={"attackDamage": {"flat": ad}}, **kw)


def _mr_item(name: str = "MR Item", mr: float = 50.0, hp: float = 300.0, **kw) -> dict:
    return _item(
        name=name,
        stats={"magicResistance": {"flat": mr}, "health": {"flat": hp}},
        **kw,
    )


def _gw_item(name: str = "Morellonomicon", ap: float = 70.0, **kw) -> dict:
    return _item(
        name=name,
        stats={"abilityPower": {"flat": ap}},
        passives=[{"name": "grievous wounds", "effects": ""}],
        **kw,
    )


def _minimal_items_dict(*items: dict) -> dict[str, dict]:
    """Build the meraki items dict format (keyed by id string)."""
    return {str(i["id"]): i for i in items}


# ─── detect_archetype ─────────────────────────────────────────────────────────

def test_mage_is_magic_ranged() -> None:
    arch = detect_archetype(_mage_champ(key="Ahri"))
    assert arch.damage_type == "magic"
    assert arch.play_style == "mage"
    assert arch.is_ranged is True
    assert arch.has_mana is True


def test_marksman_is_physical_ranged() -> None:
    arch = detect_archetype(_marksman_champ(key="Caitlyn"))
    assert arch.damage_type == "physical"
    assert arch.play_style == "marksman"
    assert arch.is_ranged is True


def test_bruiser_is_physical_melee() -> None:
    arch = detect_archetype(_bruiser_champ(key="Darius"))
    assert arch.damage_type == "physical"
    assert arch.play_style == "bruiser"
    assert arch.is_ranged is False
    assert "juggernaut" in arch.scaling_attributes


def test_tank_vanguard_role() -> None:
    arch = detect_archetype(_champ(
        key="Malphite",
        roles=["VANGUARD", "TANK"],
        positions=["TOP"],
        adaptive_type="MAGIC_DAMAGE",
    ))
    assert arch.play_style == "tank"


def test_enchanter_support_is_support() -> None:
    arch = detect_archetype(_champ(
        key="Soraka",
        roles=["ENCHANTER", "SUPPORT"],
        positions=["SUPPORT"],
        attack_type="RANGED",
        adaptive_type="MAGIC_DAMAGE",
    ))
    assert arch.play_style == "support"
    assert arch.damage_type == "magic"


def test_ap_override_akali_is_magic() -> None:
    """Akali has PHYSICAL_DAMAGE adaptiveType in Meraki but builds AP."""
    arch = detect_archetype(_champ(
        key="Akali",
        roles=["ASSASSIN", "SKIRMISHER"],
        positions=["MIDDLE", "TOP"],
        adaptive_type="PHYSICAL_DAMAGE",
    ))
    assert arch.damage_type == "magic"


def test_crit_melee_yasuo_is_marksman() -> None:
    """Yasuo is melee Fighter but builds crit like a marksman."""
    arch = detect_archetype(_champ(
        key="Yasuo",
        roles=["FIGHTER", "SKIRMISHER"],
        positions=["MIDDLE", "BOTTOM"],
        adaptive_type="PHYSICAL_DAMAGE",
    ))
    assert arch.play_style == "marksman"


def test_tank_support_thresh_item_damage_type_is_physical() -> None:
    """Thresh has MAGIC_DAMAGE adaptive but builds tank items."""
    arch = detect_archetype(_champ(
        key="Thresh",
        roles=["CATCHER", "SUPPORT", "TANK"],
        positions=["SUPPORT"],
        adaptive_type="MAGIC_DAMAGE",
    ))
    assert arch.damage_type == "magic"
    assert arch.item_damage_type == "physical"


def test_ap_assassin_gets_scaling_attribute() -> None:
    arch = detect_archetype(_champ(
        key="LeBlanc",
        roles=["MAGE", "BURST", "ASSASSIN"],
        positions=["MIDDLE"],
        attack_type="RANGED",
        adaptive_type="MAGIC_DAMAGE",
    ))
    assert "apAssassin" in arch.scaling_attributes


def test_battlemage_role_overrides_to_mage_play_style() -> None:
    arch = detect_archetype(_champ(
        key="Cassiopeia",
        roles=["BATTLEMAGE", "MAGE"],
        positions=["MIDDLE"],
        attack_type="RANGED",
        adaptive_type="MAGIC_DAMAGE",
    ))
    assert arch.play_style == "mage"
    assert "battlemage" in arch.scaling_attributes


def test_jhin_is_marksman_not_mage() -> None:
    """Jhin has MAGE tag but is MARKSMAN+BOTTOM — must stay physical marksman."""
    arch = detect_archetype(_champ(
        key="Jhin",
        roles=["MARKSMAN", "MAGE"],
        positions=["BOTTOM"],
        attack_type="RANGED",
        adaptive_type="PHYSICAL_DAMAGE",
    ))
    assert arch.play_style == "marksman"
    assert arch.damage_type == "physical"


def test_no_mana_resource() -> None:
    arch = detect_archetype(_champ(
        key="Garen",
        roles=["FIGHTER", "JUGGERNAUT"],
        resource="NONE",
    ))
    assert arch.has_mana is False


def test_primary_position_set_from_positions() -> None:
    arch = detect_archetype(_champ(
        key="X",
        roles=["FIGHTER"],
        positions=["TOP", "JUNGLE"],
    ))
    assert arch.primary_position == "TOP"


# ─── score_item ───────────────────────────────────────────────────────────────

def _mage_arch() -> ChampionArchetype:
    return detect_archetype(_mage_champ())


def _marksman_arch() -> ChampionArchetype:
    return detect_archetype(_marksman_champ())


def _bruiser_arch() -> ChampionArchetype:
    return detect_archetype(_bruiser_champ())


def test_ap_item_scores_positively_for_mage() -> None:
    arch = _mage_arch()
    s = score_item(_ap_item(ap=80.0), arch)
    assert s.score > 0
    assert any("Ability Power" in r for r in s.reasons)


def test_ad_item_scores_negatively_for_mage() -> None:
    arch = _mage_arch()
    s = score_item(_ad_item(ad=60.0), arch)
    assert s.score < 0


def test_removed_item_excluded() -> None:
    arch = _mage_arch()
    s = score_item(_ap_item(ap=100.0, removed=True), arch)
    assert s.score == pytest.approx(-999.0)


def test_unpurchasable_item_excluded() -> None:
    arch = _mage_arch()
    s = score_item(_ap_item(ap=100.0, purchasable=False), arch)
    assert s.score == pytest.approx(-999.0)


def test_tier1_item_excluded() -> None:
    arch = _mage_arch()
    s = score_item(_ap_item(ap=20.0, tier=1), arch)
    assert s.score == pytest.approx(-999.0)


def test_champion_restricted_item_excluded() -> None:
    arch = _mage_arch()
    s = score_item(_ap_item(ap=100.0, required_champion="Kalista"), arch)
    assert s.score == pytest.approx(-999.0)


def test_mr_item_boosted_vs_ap_heavy_context() -> None:
    # Bruisers already score MR positively, so context can add to it.
    arch = _bruiser_arch()
    base = score_item(_mr_item(), arch)
    ctx = GameContext(enemy_ap_count=3)
    boosted = score_item(_mr_item(), arch, ctx)
    assert boosted.score > base.score
    assert any("AP-Gegner" in r for r in boosted.reasons)


def test_gw_item_boosted_vs_sustain_context() -> None:
    arch = _mage_arch()
    ctx = GameContext(enemy_sustain_count=2)
    s = score_item(_gw_item(), arch, ctx)
    assert any("Sustain-Gegner" in r for r in s.reasons)


def test_mr_item_boosted_when_player_behind() -> None:
    arch = _bruiser_arch()
    base = score_item(_mr_item(), arch)
    ctx = GameContext(player_behind=True)
    boosted = score_item(_mr_item(), arch, ctx)
    assert boosted.score > base.score
    assert any("Golddefizit" in r for r in boosted.reasons)


def test_armor_item_boosted_vs_ad_heavy_context() -> None:
    arch = _bruiser_arch()
    armor_item = _item(
        name="Armor Item",
        stats={"armor": {"flat": 60.0}, "health": {"flat": 400.0}},
    )
    base = score_item(armor_item, arch)
    ctx = GameContext(enemy_ad_count=4)
    boosted = score_item(armor_item, arch, ctx)
    assert boosted.score > base.score
    assert any("AD-Gegner" in r for r in boosted.reasons)


def test_context_not_applied_when_score_negative() -> None:
    """Context bonuses should only apply to items that already score positively."""
    arch = _mage_arch()
    pure_ad = _ad_item()  # irrelevant for mage → score < 0
    ctx = GameContext(enemy_ap_count=5)
    s_no_ctx = score_item(pure_ad, arch)
    s_ctx = score_item(pure_ad, arch, ctx)
    assert s_ctx.score == s_no_ctx.score  # context must not rescue irrelevant items


def test_rabadon_passive_boosts_ap_mage() -> None:
    arch = _mage_arch()
    rabadon = _item(
        name="Rabadon's Deathcap",
        stats={"abilityPower": {"flat": 120.0}},
        passives=[{"name": "Magical Opus", "effects": ""}],
    )
    s = score_item(rabadon, arch)
    assert any("Rabadon" in r for r in s.reasons)


def test_grievous_wounds_static_bonus_for_mage() -> None:
    arch = _mage_arch()
    s = score_item(_gw_item(), arch)
    assert any("Grievous" in r for r in s.reasons)
    assert s.score > 0


# ─── recommend_items ─────────────────────────────────────────────────────────

def _make_big_item_dict(n: int = 20) -> dict[str, dict]:
    """Generate n distinct scoreable AP items + a few duds."""
    items = {}
    for i in range(n):
        iid = 2000 + i
        items[str(iid)] = {
            "id": iid,
            "name": f"AP Item {i}",
            "tier": 2,
            "removed": False,
            "requiredChampion": "",
            "shop": {"purchasable": True, "prices": {"total": 2800}},
            "stats": {"abilityPower": {"flat": float(50 + i)}},
            "passives": [],
        }
    # Add some duds that must be filtered out
    items["9999"] = {
        "id": 9999, "name": "Removed", "tier": 2, "removed": True,
        "requiredChampion": "", "shop": {"purchasable": True, "prices": {"total": 2600}},
        "stats": {"abilityPower": {"flat": 80.0}}, "passives": [],
    }
    items["9998"] = {
        "id": 9998, "name": "Component", "tier": 1, "removed": False,
        "requiredChampion": "", "shop": {"purchasable": True, "prices": {"total": 400}},
        "stats": {"abilityPower": {"flat": 25.0}}, "passives": [],
    }
    return items


def test_recommend_items_returns_build_result() -> None:
    champ = _mage_champ(key="Ahri")
    arch = detect_archetype(champ)
    items = _make_big_item_dict(20)
    result = recommend_items(champ, items, arch)
    assert isinstance(result, BuildResult)
    assert result.champion_name == "Ahri"


def test_core_items_capped_at_six() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(20)
    result = recommend_items(champ, items, arch)
    assert len(result.core_items) <= 6


def test_situational_items_capped_at_six() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(20)
    result = recommend_items(champ, items, arch)
    assert len(result.situational_items) <= 6


def test_no_duplicate_items_between_core_and_situational() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(20)
    result = recommend_items(champ, items, arch)
    core_names = {s.item_name for s in result.core_items}
    sit_names = {s.item_name for s in result.situational_items}
    assert core_names.isdisjoint(sit_names)


def test_removed_items_not_in_result() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(20)
    result = recommend_items(champ, items, arch)
    all_names = {s.item_name for s in result.core_items + result.situational_items}
    assert "Removed" not in all_names
    assert "Component" not in all_names


def test_archetype_passed() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(12)
    result = recommend_items(champ, items, arch)
    assert result.archetype == arch


def test_boots_name_is_none_for_cassiopeia() -> None:
    cass = _champ(
        key="Cassiopeia",
        roles=["BATTLEMAGE", "MAGE"],
        positions=["MIDDLE"],
        attack_type="RANGED",
        adaptive_type="MAGIC_DAMAGE",
    )
    arch = detect_archetype(cass)
    items = _make_big_item_dict(12)
    result = recommend_items(cass, items, arch)
    assert result.boots_name is None
    assert result.boots_id is None


def test_boots_id_is_integer_when_boots_found() -> None:
    champ = _mage_champ()
    arch = detect_archetype(champ)
    # Add a purchasable boot item to the item dict
    items = _make_big_item_dict(12)
    items["3020"] = {
        "id": 3020,
        "name": "Sorcerer's Shoes",
        "tier": 2,
        "removed": False,
        "requiredChampion": "",
        "shop": {"purchasable": True, "prices": {"total": 1100}},
        "stats": {"magicPenetration": {"flat": 18.0}},
        "passives": [],
    }
    result = recommend_items(champ, items, arch)
    if result.boots_name is not None:
        assert isinstance(result.boots_id, int)


def test_context_boosts_situational_items() -> None:
    """With sustain enemies, GW item should be ranked higher than without context."""
    champ = _mage_champ()
    arch = detect_archetype(champ)
    items = _make_big_item_dict(10)
    # Add a GW item that starts at a lower score than the generic AP items
    items["5050"] = {
        "id": 5050,
        "name": "Morellonomicon",
        "tier": 2,
        "removed": False,
        "requiredChampion": "",
        "shop": {"purchasable": True, "prices": {"total": 2500}},
        "stats": {"abilityPower": {"flat": 60.0}},
        "passives": [{"name": "grievous wounds", "effects": ""}],
    }
    no_ctx = recommend_items(champ, items, arch)
    ctx = GameContext(enemy_sustain_count=2)
    with_ctx = recommend_items(champ, items, arch, ctx)

    gw_rank_no_ctx = next(
        (i for i, s in enumerate(no_ctx.core_items + no_ctx.situational_items)
         if s.item_name == "Morellonomicon"),
        999,
    )
    gw_rank_ctx = next(
        (i for i, s in enumerate(with_ctx.core_items + with_ctx.situational_items)
         if s.item_name == "Morellonomicon"),
        999,
    )
    assert gw_rank_ctx <= gw_rank_no_ctx


# ─── build_item_set_from_result ───────────────────────────────────────────────

def _make_build_result(
    *,
    champion_name: str = "Ahri",
    core: list[tuple[int, str]] | None = None,
    situational: list[tuple[int, str]] | None = None,
    boots_name: str | None = "Sorcerer's Shoes",
    boots_id: int | None = 3020,
    starter_name: str | None = "Doran's Ring",
    starter_id: int | None = 1056,
) -> BuildResult:
    def _si(item_id: int, name: str) -> ScoredItem:
        return ScoredItem(item_id=item_id, item_name=name, score=100.0, reasons=())

    core_items = tuple(_si(iid, n) for iid, n in (core or [(2001, "Core A"), (2002, "Core B")]))
    sit_items = tuple(_si(iid, n) for iid, n in (situational or [(3001, "Sit A")]))

    return BuildResult(
        champion_name=champion_name,
        archetype=detect_archetype(_mage_champ()),
        core_items=core_items,
        situational_items=sit_items,
        boots_name=boots_name,
        boots_id=boots_id,
        starter_name=starter_name,
        starter_id=starter_id,
    )


def test_build_item_set_from_result_returns_none_for_non_build_result() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    assert build_item_set_from_result(
        champion_key="Ahri", champion_id=103, build_result="not a build result"
    ) is None


def test_build_item_set_from_result_has_correct_title() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    result = _make_build_result()
    payload = build_item_set_from_result(
        champion_key="Ahri", champion_id=103, build_result=result
    )
    assert payload is not None
    assert payload["title"] == "Champ Assistant: Ahri"
    assert payload["associatedChampions"] == [103]


def test_build_item_set_from_result_four_blocks() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    result = _make_build_result()
    payload = build_item_set_from_result(
        champion_key="Ahri", champion_id=103, build_result=result
    )
    assert payload is not None
    block_types = {b["type"] for b in payload["blocks"]}
    assert "Starting Items" in block_types
    assert "Core Build" in block_types
    assert "Boots" in block_types
    assert "Situational" in block_types


def test_build_item_set_from_result_item_ids_as_strings() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    result = _make_build_result()
    payload = build_item_set_from_result(
        champion_key="Ahri", champion_id=103, build_result=result
    )
    assert payload is not None
    for block in payload["blocks"]:
        for item in block["items"]:
            assert isinstance(item["id"], str)


def test_build_item_set_from_result_no_boots_when_none() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    result = _make_build_result(boots_name=None, boots_id=None)
    payload = build_item_set_from_result(
        champion_key="Cassiopeia", champion_id=69, build_result=result
    )
    assert payload is not None
    block_types = {b["type"] for b in payload["blocks"]}
    assert "Boots" not in block_types


def test_build_item_set_from_result_returns_none_when_no_items() -> None:
    from champ_assistant.lcu.item_sets import build_item_set_from_result
    result = BuildResult(
        champion_name="Empty",
        archetype=detect_archetype(_mage_champ()),
        core_items=(),
        situational_items=(),
        boots_name=None,
        boots_id=None,
        starter_name=None,
        starter_id=None,
    )
    payload = build_item_set_from_result(
        champion_key="Empty", champion_id=0, build_result=result
    )
    assert payload is None


# ─── rule_situational_build ───────────────────────────────────────────────────

from dataclasses import dataclass, field as dc_field


@dataclass
class _Snap:
    game_time: float = 600.0
    allies: list = dc_field(default_factory=list)
    enemies: list = dc_field(default_factory=list)
    active_summoner: str = ""
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = dc_field(default_factory=list)
    raw_events: list = dc_field(default_factory=list)
    game_result: str = ""
    new_spikes: list = dc_field(default_factory=list)


def test_rule_situational_build_returns_none_without_build_result() -> None:
    from champ_assistant.advisor.decision_engine import rule_situational_build
    snap = _Snap(game_time=300.0)
    assert rule_situational_build(snap, None) is None


def test_rule_situational_build_silent_before_two_minutes() -> None:
    from champ_assistant.advisor.decision_engine import rule_situational_build
    snap = _Snap(game_time=100.0)
    result = _make_build_result()
    assert rule_situational_build(snap, result) is None


def test_rule_situational_build_fires_after_two_minutes() -> None:
    from champ_assistant.advisor.decision_engine import rule_situational_build
    snap = _Snap(game_time=180.0)
    result = _make_build_result()
    rec = rule_situational_build(snap, result)
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "situational_build"


def test_rule_situational_build_text_contains_item_names() -> None:
    from champ_assistant.advisor.decision_engine import rule_situational_build
    snap = _Snap(game_time=300.0)
    result = _make_build_result(situational=[(3001, "Shadowflame"), (3002, "Zhonya's Hourglass")])
    rec = rule_situational_build(snap, result)
    assert rec is not None
    assert "Shadowflame" in rec.text or "Zhonya" in rec.text


def test_rule_situational_build_silent_when_no_situational_items() -> None:
    from champ_assistant.advisor.decision_engine import rule_situational_build
    snap = _Snap(game_time=300.0)
    result = BuildResult(
        champion_name="Ahri",
        archetype=detect_archetype(_mage_champ()),
        core_items=(ScoredItem(2001, "Core A", 100.0, ()),),
        situational_items=(),
        boots_name=None,
        boots_id=None,
        starter_name=None,
        starter_id=None,
    )
    assert rule_situational_build(snap, result) is None


def test_evaluate_passes_situational_build_to_rule() -> None:
    """evaluate() with situational_build= should produce a situational_build rec."""
    from champ_assistant.advisor.decision_engine import evaluate
    snap = _Snap(game_time=300.0)
    result = _make_build_result()
    recs = evaluate(snap, situational_build=result)
    kinds = [r.kind for r in recs]
    assert "situational_build" in kinds


def test_evaluate_without_situational_build_has_no_such_rec() -> None:
    from champ_assistant.advisor.decision_engine import evaluate
    snap = _Snap(game_time=300.0)
    recs = evaluate(snap)
    kinds = [r.kind for r in recs]
    assert "situational_build" not in kinds
