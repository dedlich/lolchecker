"""User-driven enemy summoner spell cooldown tracker.

The user clicks an enemy's summoner spell icon when they see it cast — the
tracker stores the game-time at which the spell was used. The remaining
cooldown is derived from ``game_time`` on every snapshot:

    remaining = (cast_at + cooldown) - game_time

When ``remaining <= 0`` the spell is treated as ready again, and the entry
disappears (next click starts a fresh timer).

Pure state, no Qt — keeps it trivial to unit-test.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpellCooldown:
    """A live cooldown entry."""

    summoner_name: str
    spell_name: str
    cast_at: float        # game_time when the user marked the spell
    cooldown: float       # seconds; 0 means "no known cooldown"

    def remaining(self, game_time: float) -> float:
        if self.cooldown <= 0:
            return 0.0
        return max(0.0, (self.cast_at + self.cooldown) - game_time)

    def is_ready(self, game_time: float) -> bool:
        return self.remaining(game_time) <= 0.0


class SpellTracker:
    """In-memory map of (summoner_name, spell_name) -> SpellCooldown."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], SpellCooldown] = {}

    def mark_used(
        self,
        summoner_name: str,
        spell_name: str,
        cooldown: float,
        game_time: float,
    ) -> SpellCooldown:
        """Record that ``summoner_name`` just used ``spell_name`` at game_time."""
        entry = SpellCooldown(
            summoner_name=summoner_name,
            spell_name=spell_name,
            cast_at=game_time,
            cooldown=cooldown,
        )
        self._entries[(summoner_name, spell_name)] = entry
        return entry

    def reset(self, summoner_name: str, spell_name: str) -> None:
        self._entries.pop((summoner_name, spell_name), None)

    def reset_summoner(self, summoner_name: str) -> None:
        for key in [k for k in self._entries if k[0] == summoner_name]:
            del self._entries[key]

    def get(self, summoner_name: str, spell_name: str) -> SpellCooldown | None:
        return self._entries.get((summoner_name, spell_name))

    def remaining(self, summoner_name: str, spell_name: str, game_time: float) -> float:
        entry = self._entries.get((summoner_name, spell_name))
        if entry is None:
            return 0.0
        return entry.remaining(game_time)

    def gc(self, game_time: float) -> int:
        """Drop ready entries; return count removed."""
        stale = [k for k, e in self._entries.items() if e.is_ready(game_time)]
        for k in stale:
            del self._entries[k]
        return len(stale)

    def __len__(self) -> int:
        return len(self._entries)

    def items(self) -> list[SpellCooldown]:
        return list(self._entries.values())
