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
