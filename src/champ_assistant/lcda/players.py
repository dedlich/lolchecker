"""Parse the LCDA ``allPlayers`` block into typed live-game players.

LCDA exposes per-player team, champion, summoner spells, items, and runes.
We only consume what the in-game UI surfaces — team + champion + the two
summoner-spell identifiers — and ignore the rest.

LCDA returns the spell's ``displayName`` localized to the client language
(e.g. German "Blitz" instead of "Flash"). For matching against our cooldown
table we use ``rawDisplayName`` which is always
``GeneratedTip_SummonerSpell_Summoner<INTERNAL>_DisplayName`` regardless of
locale. The ``<INTERNAL>`` token is what we map to the canonical English
name we use everywhere else (icon lookup, cooldown table).
"""
from __future__ import annotations

from dataclasses import dataclass

# Map LCDA's INTERNAL spell id (extracted from rawDisplayName) to the
# canonical English name. A few spells have non-obvious internal names —
# Ignite is "Dot", Cleanse is "Boost", Ghost is "Haste".
INTERNAL_TO_CANONICAL: dict[str, str] = {
    "Flash":    "Flash",
    "Dot":      "Ignite",
    "Heal":     "Heal",
    "Teleport": "Teleport",
    "Boost":    "Cleanse",
    "Barrier":  "Barrier",
    "Exhaust":  "Exhaust",
    "Smite":    "Smite",
    "Haste":    "Ghost",
    "Snowball": "Snowball",
}

# Cooldowns from Riot's data dragon ``summoner.json`` (in seconds, base — no
# Cosmic Insight reduction). Keyed on the canonical English name.
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

def _internal_id(raw_display_name: str) -> str:
    """Extract ``Flash`` / ``Dot`` / ... from
    ``GeneratedTip_SummonerSpell_SummonerFlash_DisplayName``. The string
    contains two ``Summoner`` tokens (``SummonerSpell`` and the spell-id
    one) so we anchor on the segment immediately before ``_DisplayName``
    rather than using a regex that could match either."""
    parts = raw_display_name.split("_")
    if len(parts) >= 2 and parts[-1] == "DisplayName":
        token = parts[-2]
        if token.startswith("Summoner") and len(token) > len("Summoner"):
            return token[len("Summoner"):]
    return ""


@dataclass(frozen=True)
class LiveSummonerSpell:
    name: str       # canonical English (Flash, Ignite, ...) for icon + lookup
    cooldown: float  # seconds; 0 if unknown spell


@dataclass(frozen=True)
class LivePlayer:
    summoner_name: str
    champion_name: str
    team: str  # "ORDER" or "CHAOS"
    spell_one: LiveSummonerSpell
    spell_two: LiveSummonerSpell


def _canonical_name(raw: dict) -> str:
    """Resolve a spell entry's canonical English name regardless of client
    locale. Tries rawDisplayName first (locale-independent); falls back to
    displayName (works for en_US clients and our test fixtures)."""
    internal = _internal_id(str(raw.get("rawDisplayName") or ""))
    canonical = INTERNAL_TO_CANONICAL.get(internal)
    if canonical is not None:
        return canonical
    # Fallback: trust displayName when it already matches a known spell
    # (en_US clients, tests).
    display = str(raw.get("displayName") or "").strip()
    if display in SPELL_BASE_COOLDOWN:
        return display
    return display  # unknown — keep something for the UI tooltip


def _spell(raw: dict) -> LiveSummonerSpell:
    canonical = _canonical_name(raw)
    return LiveSummonerSpell(
        name=canonical,
        cooldown=SPELL_BASE_COOLDOWN.get(canonical, 0.0),
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
