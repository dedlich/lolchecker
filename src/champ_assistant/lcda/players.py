"""Parse the LCDA ``allPlayers`` block into typed live-game players.

LCDA exposes per-player team, champion, summoner spells, items, and runes.
We only consume what the in-game UI surfaces — team + champion + the two
summoner-spell display names — and ignore the rest.
"""
from __future__ import annotations

from dataclasses import dataclass

# Cooldowns from Riot's data dragon ``summoner.json`` (in seconds, base — no
# Cosmic Insight reduction). Keys are the *raw* spell display name LCDA returns
# in ``summonerSpells.summonerSpellOne.displayName``.
SPELL_BASE_COOLDOWN: dict[str, float] = {
    "Flash": 300.0,
    "Ignite": 180.0,
    "Heal": 240.0,
    "Teleport": 240.0,  # Unleashed Teleport varies; keep base for v1.
    "Cleanse": 210.0,
    "Barrier": 180.0,
    "Exhaust": 210.0,
    "Smite": 90.0,
    "Ghost": 210.0,
    "Snowball": 80.0,  # Mark/Dash on ARAM
}


@dataclass(frozen=True)
class LiveSummonerSpell:
    name: str
    cooldown: float  # seconds; 0 if unknown spell


@dataclass(frozen=True)
class LivePlayer:
    summoner_name: str
    champion_name: str
    team: str  # "ORDER" or "CHAOS"
    spell_one: LiveSummonerSpell
    spell_two: LiveSummonerSpell


def _spell(raw: dict) -> LiveSummonerSpell:
    name = str(raw.get("displayName") or "").strip()
    return LiveSummonerSpell(
        name=name,
        cooldown=SPELL_BASE_COOLDOWN.get(name, 0.0),
    )


def parse_players(all_players: list[dict]) -> list[LivePlayer]:
    """Project the raw LCDA player list into ``LivePlayer`` records."""
    players: list[LivePlayer] = []
    for entry in all_players:
        spells = entry.get("summonerSpells") or {}
        players.append(
            LivePlayer(
                summoner_name=str(entry.get("summonerName") or ""),
                champion_name=str(entry.get("championName") or ""),
                team=str(entry.get("team") or ""),
                spell_one=_spell(spells.get("summonerSpellOne") or {}),
                spell_two=_spell(spells.get("summonerSpellTwo") or {}),
            )
        )
    return players


def enemies_of(players: list[LivePlayer], active_team: str) -> list[LivePlayer]:
    """Return everyone *not* on the active player's team."""
    if not active_team:
        return list(players)
    return [p for p in players if p.team and p.team != active_team]
