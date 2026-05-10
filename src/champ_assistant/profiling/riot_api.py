"""Thin async client for the Riot Web API.

Endpoints we use (Riot returns JSON, plain Bearer-style auth via header):
  /lol/summoner/v4/summoners/by-puuid/{puuid}                 (platform host)
  /lol/league/v4/entries/by-puuid/{puuid}                     (platform host)
  /lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top
  /lol/match/v5/matches/by-puuid/{puuid}/ids?count=10         (regional host)
  /lol/match/v5/matches/{matchId}                              (regional host)

Riot retired the by-name and by-summoner-id endpoints during the Riot ID
migration — every lookup now goes through PUUID. The LCU session payload
gives us puuid for every team member, so we drive the whole pipeline from
that one identifier.

Region routing: Riot splits hosts into platform routes (per-server, e.g.
``euw1.api.riotgames.com``) and regional routes (continent groups, e.g.
``europe.api.riotgames.com``). Match-V5 lives on regional, Summoner /
League / Mastery on platform.

Errors are mapped to ``RiotApiError`` so the UI can degrade silently
when the user has no key, the key is rate-limited (429), or the player
isn't found (404).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PLATFORM_HOSTS: dict[str, str] = {
    "EUW": "euw1.api.riotgames.com",
    "EUNE": "eun1.api.riotgames.com",
    "NA": "na1.api.riotgames.com",
    "KR": "kr.api.riotgames.com",
    "JP": "jp1.api.riotgames.com",
    "BR": "br1.api.riotgames.com",
    "LAN": "la1.api.riotgames.com",
    "LAS": "la2.api.riotgames.com",
    "OCE": "oc1.api.riotgames.com",
    "TR": "tr1.api.riotgames.com",
    "RU": "ru.api.riotgames.com",
}

# Maps a platform region to its regional cluster for Match-V5.
REGIONAL_CLUSTER: dict[str, str] = {
    "EUW": "europe.api.riotgames.com",
    "EUNE": "europe.api.riotgames.com",
    "TR": "europe.api.riotgames.com",
    "RU": "europe.api.riotgames.com",
    "NA": "americas.api.riotgames.com",
    "BR": "americas.api.riotgames.com",
    "LAN": "americas.api.riotgames.com",
    "LAS": "americas.api.riotgames.com",
    "KR": "asia.api.riotgames.com",
    "JP": "asia.api.riotgames.com",
    "OCE": "sea.api.riotgames.com",
}


class RiotApiError(RuntimeError):
    """Wrapper for failed Riot API calls."""


@dataclass(frozen=True)
class SummonerInfo:
    puuid: str
    summoner_id: str
    name: str
    level: int


@dataclass(frozen=True)
class MasteryEntry:
    champion_id: int
    points: int
    level: int


@dataclass(frozen=True)
class RankEntry:
    """One Riot ranked-queue entry (e.g. solo/duo or flex)."""
    queue_type: str   # "RANKED_SOLO_5x5" | "RANKED_FLEX_SR"
    tier: str         # IRON, BRONZE, SILVER, GOLD, PLATINUM, EMERALD, DIAMOND, MASTER, GRANDMASTER, CHALLENGER, "" if unranked
    division: str     # I, II, III, IV (empty for MASTER+)
    league_points: int
    wins: int
    losses: int

    @property
    def games(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.games if self.games else None


class RiotApiClient:
    DEFAULT_TIMEOUT = 5.0

    def __init__(
        self,
        api_key: str,
        *,
        region: str = "EUW",
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.region = region.upper()
        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "headers": {"X-Riot-Token": api_key},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

    def set_credentials(self, *, api_key: str, region: str | None = None) -> None:
        """Update credentials in place. Mirrors the pattern used by
        ``GamePlanLLMService`` / ``RuntimeCounterStore`` so the running
        ``httpx.AsyncClient`` (and its connection pool) survives a
        settings save — previously ``_on_settings_changed`` rebuilt
        ProfileService from scratch, leaking the old client every time.
        """
        self._api_key = api_key
        self._client.headers["X-Riot-Token"] = api_key
        if region:
            self.region = region.upper()

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    @property
    def _platform(self) -> str:
        return PLATFORM_HOSTS.get(self.region, PLATFORM_HOSTS["EUW"])

    @property
    def _regional(self) -> str:
        return REGIONAL_CLUSTER.get(self.region, REGIONAL_CLUSTER["EUW"])

    async def _get(self, host: str, path: str) -> Any:
        url = f"https://{host}{path}"
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise RiotApiError(f"network error: {exc}") from exc
        if response.status_code == 404:
            raise RiotApiError(f"not found: {path}")
        if response.status_code == 401:
            raise RiotApiError("invalid Riot API key")
        if response.status_code == 429:
            raise RiotApiError("rate-limited")
        if not (200 <= response.status_code < 300):
            raise RiotApiError(
                f"riot api {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RiotApiError(f"bad json: {exc}") from exc

    async def summoner_by_puuid(self, puuid: str) -> SummonerInfo:
        path = f"/lol/summoner/v4/summoners/by-puuid/{puuid}"
        data = await self._get(self._platform, path)
        return self._summoner_from(data)

    async def riot_id_by_puuid(self, puuid: str) -> tuple[str, str]:
        """Resolve a puuid to its current Riot ID (``GameName#TagLine``).
        Riot's ``summoner-v4`` returns an empty ``name`` field for any
        account that migrated to the Riot ID system (i.e. all modern
        accounts), so the public source of truth for display names is
        ``account-v1/by-puuid``. Returns ``("", "")`` on failure so
        callers can fall back to a placeholder.

        Retries on 429 (rate-limit) up to ~9s of total backoff because
        the loading-screen path fires this 9 times in a tight burst —
        Riot's dev-key rate limit is 20/s and the previous one-shot
        path silently dropped 6 of 9 names per game.
        """
        import asyncio as _asyncio
        path = f"/riot/account/v1/accounts/by-puuid/{puuid}"
        for attempt in range(4):
            try:
                data = await self._get(self._regional, path)
            except RiotApiError as exc:
                # Only retry on rate-limit; 4xx/network errors won't fix
                # themselves with a wait.
                if "rate-limited" in str(exc) and attempt < 3:
                    await _asyncio.sleep(1.0 + attempt * 1.5)
                    continue
                logger.info("account_by_puuid_failed: %s", exc)
                return ("", "")
            if isinstance(data, dict):
                game_name = str(data.get("gameName") or "")
                tag_line = str(data.get("tagLine") or "")
                return (game_name, tag_line)
            return ("", "")
        return ("", "")

    async def puuid_by_riot_id(self, game_name: str, tag_line: str) -> str:
        """Resolve a Riot ID (``GameName#TagLine``) to a real
        78-character puuid. Riot's LCU now privacy-strips puuids for
        every account — even the local player's — so the LCU surfaces
        only synthetic UUIDs. ``account-v1/by-riot-id`` is the supported
        public endpoint that decrypts the Riot ID into a real puuid we
        can pass to spectator / mastery / league endpoints. URL-encodes
        both segments to handle spaces and special characters in
        display names. Returns ``""`` on any failure so callers can
        fall back gracefully.
        """
        from urllib.parse import quote
        encoded_name = quote(game_name, safe="")
        encoded_tag = quote(tag_line, safe="")
        path = (
            f"/riot/account/v1/accounts/by-riot-id/"
            f"{encoded_name}/{encoded_tag}"
        )
        try:
            data = await self._get(self._regional, path)
        except RiotApiError as exc:
            logger.info("account_by_riot_id_failed: %s", exc)
            return ""
        if isinstance(data, dict):
            puuid = data.get("puuid")
            if isinstance(puuid, str) and len(puuid) > 60:
                return puuid
        return ""

    async def active_game_participants(self, puuid: str) -> list[dict[str, Any]]:
        """Spectator-v5: list of every participant in the puuid's current
        active match. Returns ``[]`` on 404 (no active game) or any
        other error. Each entry carries the participant's real
        ``puuid`` and ``championId`` — that's the bridge we need from
        a champ-select cell_id to a Riot Web API-resolvable puuid,
        because ``/lol-gameflow/v1/session`` only exposes synthetic
        UUID-format puuids that the public API rejects.
        """
        path = f"/lol/spectator/v5/active-games/by-summoner/{puuid}"
        try:
            data = await self._get(self._platform, path)
        except RiotApiError as exc:
            logger.info("spectator_active_game_failed: %s", exc)
            return []
        participants = data.get("participants") if isinstance(data, dict) else None
        if not isinstance(participants, list):
            return []
        return [p for p in participants if isinstance(p, dict)]

    @staticmethod
    def _summoner_from(data: Any) -> SummonerInfo:
        return SummonerInfo(
            puuid=str(data.get("puuid") or ""),
            summoner_id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            level=int(data.get("summonerLevel") or 0),
        )

    async def league_entries_by_puuid(self, puuid: str) -> list[RankEntry]:
        """Return ranked entries for the player, one per queue. Returns
        an empty list when the player is unranked or the call fails.
        Endpoint replacement for the deprecated by-summoner form —
        production keys only grant the by-puuid variant."""
        path = f"/lol/league/v4/entries/by-puuid/{puuid}"
        try:
            data = await self._get(self._platform, path)
        except RiotApiError as exc:
            logger.info("league_entries_failed: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        out: list[RankEntry] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            out.append(RankEntry(
                queue_type=str(entry.get("queueType") or ""),
                tier=str(entry.get("tier") or ""),
                division=str(entry.get("rank") or ""),
                league_points=int(entry.get("leaguePoints") or 0),
                wins=int(entry.get("wins") or 0),
                losses=int(entry.get("losses") or 0),
            ))
        return out

    async def top_mastery(
        self, puuid: str, *, count: int = 3
    ) -> list[MasteryEntry]:
        path = (
            f"/lol/champion-mastery/v4/champion-masteries"
            f"/by-puuid/{puuid}/top?count={count}"
        )
        data = await self._get(self._platform, path)
        if not isinstance(data, list):
            return []
        return [
            MasteryEntry(
                champion_id=int(e.get("championId") or 0),
                points=int(e.get("championPoints") or 0),
                level=int(e.get("championLevel") or 0),
            )
            for e in data
            if isinstance(e, dict)
        ]

    async def recent_match_ids(
        self, puuid: str, *, count: int = 10, queue: int | None = 420
    ) -> list[str]:
        """Recent ranked-solo (queue 420) match IDs by default."""
        suffix = f"?count={count}"
        if queue is not None:
            suffix += f"&queue={queue}"
        path = f"/lol/match/v5/matches/by-puuid/{puuid}/ids{suffix}"
        data = await self._get(self._regional, path)
        if not isinstance(data, list):
            return []
        return [str(x) for x in data]

    async def match_outcome(self, match_id: str, puuid: str) -> bool | None:
        """Return True/False (win/loss) for the given player, or None.

        Kept for back-compat — new code should use
        ``match_participant_info`` which returns the richer dict
        without an extra fetch."""
        info = await self.match_participant_info(match_id, puuid)
        if info is None:
            return None
        return info.get("win")

    async def match_participant_info(
        self, match_id: str, puuid: str,
    ) -> dict | None:
        """Return ``{"win": bool, "role": str}`` for ``puuid`` in
        ``match_id``. Role is normalized to our domain
        (TOP/JUNGLE/MID/BOT/SUPPORT) from Riot's ``teamPosition``.
        ``None`` if the participant isn't found or the response
        shape is unexpected."""
        from ..data.models import normalize_role
        path = f"/lol/match/v5/matches/{match_id}"
        data = await self._get(self._regional, path)
        info = data.get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict):
            return None
        for participant in info.get("participants") or []:
            if (
                isinstance(participant, dict)
                and participant.get("puuid") == puuid
            ):
                team_pos = participant.get("teamPosition") or ""
                role = normalize_role(team_pos)
                return {
                    "win": bool(participant.get("win")),
                    "role": role,
                }
        return None

    async def recent_match_summaries(
        self,
        puuid: str,
        *,
        count: int = 20,
        queue: int | None = 420,
    ) -> list[dict]:
        """Fetch ``count`` recent ranked-solo match summaries — one
        ``{win, role}`` dict per match. Failed fetches are silently
        skipped. Used by ProfileService to compute streak +
        per-role winrate from a single set of match-v5 calls."""
        try:
            ids = await self.recent_match_ids(puuid, count=count, queue=queue)
        except RiotApiError:
            return []
        if not ids:
            return []
        results = await asyncio.gather(
            *(self.match_participant_info(mid, puuid) for mid in ids),
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]


def role_winrate_from_summaries(
    summaries: list[dict],
) -> dict[str, tuple[int, int]]:
    """Aggregate ``recent_match_summaries`` output into
    ``{role: (wins, losses)}``. Pure function, testable without
    the API client."""
    out: dict[str, list[int]] = {}
    for s in summaries:
        role = s.get("role")
        if not role:
            continue
        bucket = out.setdefault(role, [0, 0])
        if s.get("win"):
            bucket[0] += 1
        else:
            bucket[1] += 1
    return {role: (w, l) for role, (w, l) in out.items()}


def streak_from_summaries(summaries: list[dict]) -> tuple[int, int, int]:
    """Aggregate summaries into ``(wins, losses, streak)``. Streak
    is positive on a win run from the most-recent match backwards,
    negative on a loss run. Failed/unknown matches break the run."""
    wins = sum(1 for s in summaries if s.get("win") is True)
    losses = sum(1 for s in summaries if s.get("win") is False)
    streak = 0
    for s in summaries:
        win = s.get("win")
        if not isinstance(win, bool):
            break
        if streak == 0:
            streak = 1 if win else -1
            continue
        if (win and streak > 0) or (not win and streak < 0):
            streak += 1 if win else -1
        else:
            break
    return wins, losses, streak

