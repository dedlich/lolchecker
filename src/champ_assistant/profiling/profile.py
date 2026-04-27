"""High-level enemy profile aggregator.

Composes multiple Riot API calls into a single ``EnemyProfile`` per
summoner. Caches results in-process for the duration of a champ-select —
profiles don't change once the lobby starts.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .riot_api import RiotApiClient, RiotApiError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopChampion:
    champion_id: int
    points: int
    mastery_level: int


@dataclass(frozen=True)
class EnemyProfile:
    summoner_name: str
    level: int = 0
    top_champions: list[TopChampion] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    streak: int = 0  # positive = win streak, negative = loss streak

    @property
    def win_rate(self) -> float | None:
        total = self.wins + self.losses
        if total == 0:
            return None
        return self.wins / total

    @property
    def has_data(self) -> bool:
        return bool(self.top_champions) or (self.wins + self.losses) > 0


class ProfileService:
    """Builds + caches profiles for the duration of a champ-select session."""

    def __init__(self, client: RiotApiClient) -> None:
        self._client = client
        self._cache: dict[str, EnemyProfile] = {}

    @property
    def enabled(self) -> bool:
        return self._client.enabled

    def cached(self, summoner_name: str) -> EnemyProfile | None:
        return self._cache.get(summoner_name.lower())

    async def fetch(self, summoner_name: str) -> EnemyProfile:
        """Fetch + cache a profile. Errors degrade to an empty profile."""
        key = summoner_name.lower()
        if key in self._cache:
            return self._cache[key]
        try:
            summoner = await self._client.summoner_by_name(summoner_name)
        except RiotApiError as exc:
            logger.info("profile_summoner_failed name=%s: %s", summoner_name, exc)
            empty = EnemyProfile(summoner_name=summoner_name)
            self._cache[key] = empty
            return empty

        mastery_task = asyncio.create_task(
            self._client.top_mastery(summoner.puuid, count=3)
        )
        streak_task = asyncio.create_task(
            self._client.win_loss_streak(summoner.puuid)
        )
        try:
            mastery = await mastery_task
        except RiotApiError as exc:
            logger.info("profile_mastery_failed: %s", exc)
            mastery = []
        try:
            wins, losses, streak = await streak_task
        except RiotApiError as exc:
            logger.info("profile_streak_failed: %s", exc)
            wins, losses, streak = 0, 0, 0

        profile = EnemyProfile(
            summoner_name=summoner.name or summoner_name,
            level=summoner.level,
            top_champions=[
                TopChampion(
                    champion_id=m.champion_id,
                    points=m.points,
                    mastery_level=m.level,
                )
                for m in mastery
            ],
            wins=wins,
            losses=losses,
            streak=streak,
        )
        self._cache[key] = profile
        return profile

    def clear(self) -> None:
        self._cache.clear()
