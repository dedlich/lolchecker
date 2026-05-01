"""Tests for the LCDA player parser."""
from __future__ import annotations

from champ_assistant.lcda.players import (
    SPELL_BASE_COOLDOWN,
    enemies_of,
    parse_players,
)


def test_parse_players_extracts_summoner_spells() -> None:
    raw = [
        {
            "summonerName": "Faker",
            "championName": "Ahri",
            "team": "ORDER",
            "summonerSpells": {
                "summonerSpellOne": {"displayName": "Flash"},
                "summonerSpellTwo": {"displayName": "Ignite"},
            },
        }
    ]
    players = parse_players(raw)
    assert len(players) == 1
    p = players[0]
    assert p.summoner_name == "Faker"
    assert p.champion_name == "Ahri"
    assert p.team == "ORDER"
    assert p.spell_one.name == "Flash"
    assert p.spell_one.cooldown == SPELL_BASE_COOLDOWN["Flash"]
    assert p.spell_two.name == "Ignite"
    assert p.spell_two.cooldown == SPELL_BASE_COOLDOWN["Ignite"]


def test_parse_handles_missing_fields() -> None:
    raw = [{"summonerName": "X", "championName": "Garen"}]
    players = parse_players(raw)
    assert players[0].team == ""
    assert players[0].spell_one.name == ""
    assert players[0].spell_one.cooldown == 0.0


def test_parse_unknown_spell_has_zero_cooldown() -> None:
    raw = [{
        "summonerName": "X", "championName": "Y", "team": "ORDER",
        "summonerSpells": {
            "summonerSpellOne": {"displayName": "TotallyMadeUp"},
            "summonerSpellTwo": {"displayName": "Smite"},
        },
    }]
    p = parse_players(raw)[0]
    assert p.spell_one.cooldown == 0.0
    assert p.spell_two.cooldown == 90.0  # smite


def test_enemies_of_filters_by_team() -> None:
    raw = [
        {"summonerName": "A", "championName": "Ashe", "team": "ORDER",
         "summonerSpells": {"summonerSpellOne": {"displayName": "Flash"},
                            "summonerSpellTwo": {"displayName": "Heal"}}},
        {"summonerName": "B", "championName": "Annie", "team": "CHAOS",
         "summonerSpells": {"summonerSpellOne": {"displayName": "Flash"},
                            "summonerSpellTwo": {"displayName": "Ignite"}}},
        {"summonerName": "C", "championName": "Lux", "team": "CHAOS",
         "summonerSpells": {"summonerSpellOne": {"displayName": "Flash"},
                            "summonerSpellTwo": {"displayName": "Barrier"}}},
    ]
    players = parse_players(raw)
    enemies = enemies_of(players, active_team="ORDER")
    assert {p.champion_name for p in enemies} == {"Annie", "Lux"}


def test_enemies_of_returns_all_when_team_unknown() -> None:
    raw = [{"summonerName": "A", "championName": "Ashe", "team": "ORDER",
            "summonerSpells": {}}]
    players = parse_players(raw)
    assert len(enemies_of(players, active_team="")) == 1


def test_german_locale_resolves_via_rawDisplayName() -> None:
    """When LCDA returns localized displayName ('Blitz', 'Entzuenden') we must
    still resolve the spell to its canonical English name + correct cooldown
    via rawDisplayName. Regression test for the v0.10.5 click-does-nothing bug
    on German clients."""
    raw = [{
        "summonerName": "X", "championName": "Ahri", "team": "CHAOS",
        "summonerSpells": {
            "summonerSpellOne": {
                "displayName": "Blitz",
                "rawDisplayName":
                    "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName",
            },
            "summonerSpellTwo": {
                "displayName": "Entzuenden",
                "rawDisplayName":
                    "GeneratedTip_SummonerSpell_SummonerDot_DisplayName",
            },
        },
    }]
    p = parse_players(raw)[0]
    assert p.spell_one.name == "Flash"
    assert p.spell_one.cooldown == 300.0
    assert p.spell_two.name == "Ignite"
    assert p.spell_two.cooldown == 180.0


def test_canonical_resolution_for_each_known_spell() -> None:
    cases = [
        ("SummonerFlash",    "Flash",    300.0),
        ("SummonerDot",      "Ignite",   180.0),
        ("SummonerHeal",     "Heal",     240.0),
        ("SummonerTeleport", "Teleport", 240.0),
        ("SummonerBoost",    "Cleanse",  210.0),
        ("SummonerBarrier",  "Barrier",  180.0),
        ("SummonerExhaust",  "Exhaust",  210.0),
        ("SummonerSmite",    "Smite",    90.0),
        ("SummonerHaste",    "Ghost",    210.0),
        ("SummonerSnowball", "Snowball", 80.0),
    ]
    for internal, canonical, cooldown in cases:
        raw = [{
            "summonerName": "X", "championName": "Y", "team": "CHAOS",
            "summonerSpells": {
                "summonerSpellOne": {
                    "displayName": "anything-localized",
                    "rawDisplayName":
                        f"GeneratedTip_SummonerSpell_{internal}_DisplayName",
                },
                "summonerSpellTwo": {},
            },
        }]
        p = parse_players(raw)[0]
        assert p.spell_one.name == canonical, internal
        assert p.spell_one.cooldown == cooldown, internal


def test_parse_position_field() -> None:
    """LCDA ``position`` is uppercased and stored on LivePlayer."""
    for role in ("JUNGLE", "TOP", "MIDDLE", "BOTTOM", "UTILITY"):
        raw = [{"summonerName": "X", "championName": "Y", "team": "ORDER",
                "position": role, "summonerSpells": {}}]
        p = parse_players(raw)[0]
        assert p.position == role


def test_parse_position_lowercased_input_uppercased() -> None:
    raw = [{"summonerName": "X", "championName": "Y", "team": "ORDER",
            "position": "jungle", "summonerSpells": {}}]
    p = parse_players(raw)[0]
    assert p.position == "JUNGLE"


def test_parse_position_defaults_to_empty_string() -> None:
    raw = [{"summonerName": "X", "championName": "Y", "team": "ORDER",
            "summonerSpells": {}}]
    p = parse_players(raw)[0]
    assert p.position == ""
