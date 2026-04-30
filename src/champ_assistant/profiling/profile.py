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
class RankBadge:
    """Solo/duo ranked snapshot — tier + division + LP + wins/losses."""
    tier: str = ""        # "DIAMOND" / "" if unranked
    division: str = ""    # "II" / "" for MASTER+
    league_points: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def is_ranked(self) -> bool:
        return bool(self.tier)

    @property
    def short(self) -> str:
        if not self.tier:
            return "Unranked"
        if self.tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{self.tier.title()} {self.league_points}LP"
        return f"{self.tier.title()} {self.division} {self.league_points}LP"


@dataclass(frozen=True)
class EnemyProfile:
    summoner_name: str
    level: int = 0
    top_champions: list[TopChampion] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    streak: int = 0  # positive = win streak, negative = loss streak
    rank: RankBadge = field(default_factory=RankBadge)
    role_winrates: dict[str, tuple[int, int]] = field(default_factory=dict)
    """``{role: (wins, losses)}`` over the last ~20 ranked-solo matches.
    Drives the lobby panel's "main role" indicator + per-role winrate
    badge. Empty dict means no data (no API key, fetch failed, or
    player has no recent ranked matches)."""

    @property
    def win_rate(self) -> float | None:
        total = self.wins + self.losses
        if total == 0:
            return None
        return self.wins / total

    @property
    def main_role(self) -> str | None:
        """The role this player has played most over the recent
        sample. ``None`` when role_winrates is empty or there's a
        tie (be conservative — don't pick arbitrarily)."""
        if not self.role_winrates:
            return None
        ranked = sorted(
            self.role_winrates.items(),
            key=lambda kv: kv[1][0] + kv[1][1],
            reverse=True,
        )
        top_games = ranked[0][1][0] + ranked[0][1][1]
        if top_games == 0:
            return None
        # Tied with 2nd place → ambiguous, return None.
        if len(ranked) > 1:
            second_games = ranked[1][1][0] + ranked[1][1][1]
            if second_games == top_games:
                return None
        return ranked[0][0]

    def role_summary(self, role: str) -> str | None:
        """Render ``"56% (28W/22L)"`` for ``role``, or None when no
        data. Used by the EnemyRow badge."""
        wl = self.role_winrates.get(role)
        if wl is None:
            return None
        wins, losses = wl
        total = wins + losses
        if total == 0:
            return None
        return f"{int(100 * wins / total)}% ({wins}W/{losses}L)"

    @property
    def has_data(self) -> bool:
        return (
            bool(self.top_champions)
            or (self.wins + self.losses) > 0
            or self.rank.is_ranked
            or bool(self.role_winrates)
            or self.level > 0
        )

    @property
    def behavior_tags(self) -> list[str]:
        """Curated short-form labels derived from the rest of the
        profile. Pure function over the existing fields — no extra
        API fetches needed. Returns sorted shortest-to-longest so
        the UI can pack them tightly."""
        return compute_behavior_tags(self)


# ----------------------------------------------------------------------
# Behavior-tag computation — pure function, testable without HTTP
# ----------------------------------------------------------------------

# Role-share threshold for "this player only plays X" labelling.
# 70%+ of the recent-20 sample on one role = pretty solid OTP.
OTP_ROLE_SHARE = 0.70
# Below this share for the most-played role we suspect autofill —
# "they play too many roles to claim a main".
AUTOFILL_TOP_SHARE = 0.45
# Streak length that flips into hot/cold pills.
HOT_STREAK = 4
COLD_STREAK = -4
# Mastery point threshold for "champ specialist" tag — 500k is the
# Riot point where champion-mastery levels max out at 7.
HIGH_MASTERY_POINTS = 500_000
# Account-level brackets — purely informational.
VETERAN_LEVEL = 250
NEWBIE_LEVEL = 50


def compute_behavior_tags(profile: "EnemyProfile") -> list[str]:
    """Derive a compact set of behavior labels from an EnemyProfile.
    Empty list when the profile has no data yet (fetch in flight).
    """
    if not profile.has_data:
        return []

    tags: list[str] = []

    # Role-play patterns.
    if profile.role_winrates:
        total_games = sum(w + l for w, l in profile.role_winrates.values())
        if total_games > 0:
            shares = {
                role: (w + l) / total_games
                for role, (w, l) in profile.role_winrates.items()
            }
            top_role, top_share = max(shares.items(), key=lambda kv: kv[1])
            if top_share >= OTP_ROLE_SHARE:
                tags.append(f"OTP {top_role}")
            elif top_share < AUTOFILL_TOP_SHARE:
                tags.append("Autofill?")

    # Recent-form streaks.
    if profile.streak >= HOT_STREAK:
        tags.append(f"Hot W{profile.streak}")
    elif profile.streak <= COLD_STREAK:
        tags.append(f"Tilt L{abs(profile.streak)}")

    # Champion specialist — top mastery is meaningfully high.
    if profile.top_champions:
        top = profile.top_champions[0]
        if top.points >= HIGH_MASTERY_POINTS:
            tags.append("Champ-Spec")

    # Account level — informational, not actionable.
    if profile.level >= VETERAN_LEVEL:
        tags.append("Veteran")
    elif 0 < profile.level <= NEWBIE_LEVEL:
        tags.append("Newbie")

    return tags


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

    def cached_by_id(self, key: str) -> EnemyProfile | None:
        return self._cache.get(key)

    async def fetch_by_puuid(self, puuid: str) -> EnemyProfile:
        """Fetch + cache by puuid (preferred — Riot's identifier of choice)."""
        cache_key = f"puuid:{puuid}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            summoner = await self._client.summoner_by_puuid(puuid)
        except RiotApiError as exc:
            logger.info("profile_lookup_by_puuid_failed: %s", exc)
            empty = EnemyProfile(summoner_name=puuid[:8])
            self._cache[cache_key] = empty
            return empty
        return await self._compose(summoner, cache_key)

    async def fetch_by_summoner_id(self, summoner_id: int | str) -> EnemyProfile:
        """Legacy fallback for ancient LCU payloads without puuid.
        Riot retired the by-summoner-id summoner lookup, so we can't
        resolve the player here — cache an empty profile to stop the
        UI from retrying. Modern LCU always sends puuid; this path
        is essentially never taken."""
        cache_key = f"sid:{summoner_id}"
        if cache_key not in self._cache:
            self._cache[cache_key] = EnemyProfile(
                summoner_name=str(summoner_id)[:8],
            )
        return self._cache[cache_key]

    async def _compose(self, summoner: object, cache_key: str) -> EnemyProfile:
        """Shared post-summoner-lookup pipeline: mastery + match
        summaries + rank all fanned out concurrently. Match summaries
        feed both streak AND per-role winrate — single fetch path,
        no double polling of match-v5."""
        from .riot_api import (
            SummonerInfo,
            role_winrate_from_summaries,
            streak_from_summaries,
        )
        assert isinstance(summoner, SummonerInfo)
        mastery_task = asyncio.create_task(
            self._client.top_mastery(summoner.puuid, count=3)
        )
        summaries_task = asyncio.create_task(
            self._client.recent_match_summaries(summoner.puuid, count=20)
        )
        rank_task = asyncio.create_task(
            self._client.league_entries_by_puuid(summoner.puuid)
        )

        try:
            mastery = await mastery_task
        except RiotApiError as exc:
            logger.info("profile_mastery_failed: %s", exc)
            mastery = []
        try:
            summaries = await summaries_task
        except RiotApiError as exc:
            logger.info("profile_summaries_failed: %s", exc)
            summaries = []
        wins, losses, streak = streak_from_summaries(summaries)
        role_winrates = role_winrate_from_summaries(summaries)

        rank = RankBadge()
        try:
            entries = await rank_task
        except RiotApiError as exc:
            logger.info("profile_rank_failed: %s", exc)
            entries = []
        solo = next(
            (e for e in entries if e.queue_type == "RANKED_SOLO_5x5"),
            None,
        )
        chosen = solo or (entries[0] if entries else None)
        if chosen is not None:
            rank = RankBadge(
                tier=chosen.tier,
                division=chosen.division,
                league_points=chosen.league_points,
                wins=chosen.wins,
                losses=chosen.losses,
            )

        profile = EnemyProfile(
            summoner_name=summoner.name or cache_key,
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
            rank=rank,
            role_winrates=role_winrates,
        )
        self._cache[cache_key] = profile
        return profile

    def clear(self) -> None:
        self._cache.clear()
