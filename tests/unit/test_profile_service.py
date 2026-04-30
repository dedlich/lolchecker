"""Tests for the ProfileService aggregator."""
from __future__ import annotations

import httpx
import pytest

from champ_assistant.profiling.profile import ProfileService
from champ_assistant.profiling.riot_api import RiotApiClient


def _client(payloads: dict[str, dict | list]) -> RiotApiClient:
    """payloads maps url-substring -> JSON body."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for needle, body in payloads.items():
            if needle in url:
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    return RiotApiClient(
        "key", region="EUW",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_fetch_by_puuid_returns_full_profile() -> None:
    client = _client({
        "summoner/v4/summoners/by-puuid/": {
            "puuid": "PUUID", "id": "S", "name": "Faker", "summonerLevel": 800,
        },
        "league/v4/entries/by-puuid/": [
            {
                "queueType": "RANKED_SOLO_5x5",
                "tier": "DIAMOND", "rank": "II",
                "leaguePoints": 24, "wins": 80, "losses": 70,
            },
        ],
        "champion-mastery": [
            {"championId": 103, "championPoints": 500_000, "championLevel": 7},
            {"championId": 64, "championPoints": 250_000, "championLevel": 7},
            {"championId": 90, "championPoints": 200_000, "championLevel": 6},
        ],
        "/ids": ["M1", "M2"],
        "/matches/M": {
            "info": {"participants": [{"puuid": "PUUID", "win": True}]},
        },
    })
    service = ProfileService(client)
    profile = await service.fetch_by_puuid("PUUID")
    assert profile.summoner_name == "Faker"
    assert profile.level == 800
    assert [c.champion_id for c in profile.top_champions] == [103, 64, 90]
    assert profile.wins == 2
    assert profile.losses == 0
    assert profile.streak == 2
    assert profile.win_rate == 1.0
    # Rank surfaces too — we hit the by-puuid league endpoint, not the
    # deprecated by-summoner-id one.
    assert profile.rank.tier == "DIAMOND"
    assert profile.rank.division == "II"
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_by_puuid_caches_per_session() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        url = str(request.url)
        if "summoner/v4" in url:
            return httpx.Response(200, json={
                "puuid": "P", "id": "S", "name": "X", "summonerLevel": 10,
            })
        if "league/v4" in url:
            return httpx.Response(200, json=[])
        if "champion-mastery" in url:
            return httpx.Response(200, json=[])
        if "/ids" in url:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    client = RiotApiClient(
        "key", region="EUW",
        transport=httpx.MockTransport(handler),
    )
    service = ProfileService(client)
    await service.fetch_by_puuid("P")
    first_calls = calls["n"]
    await service.fetch_by_puuid("P")  # should hit the cache
    assert calls["n"] == first_calls
    await client.aclose()


@pytest.mark.asyncio
async def test_disabled_when_no_key() -> None:
    client = RiotApiClient("", region="EUW")
    service = ProfileService(client)
    assert service.enabled is False
    await client.aclose()


def test_enemy_profile_main_role_picks_most_played() -> None:
    """The main_role property returns the role with the most games
    over the recent ranked sample."""
    from champ_assistant.profiling.profile import EnemyProfile
    profile = EnemyProfile(
        summoner_name="X",
        role_winrates={"TOP": (8, 4), "MID": (2, 1), "JUNGLE": (3, 2)},
    )
    assert profile.main_role == "TOP"


def test_enemy_profile_main_role_returns_none_on_tie() -> None:
    """Tied game counts → ambiguous, main_role recuses itself rather
    than picking arbitrarily. Avoids "TOP main" claims for autofill
    players who play TOP/JUNGLE 50/50."""
    from champ_assistant.profiling.profile import EnemyProfile
    profile = EnemyProfile(
        summoner_name="X",
        role_winrates={"TOP": (5, 5), "MID": (5, 5)},
    )
    assert profile.main_role is None


def test_enemy_profile_role_summary_renders_winrate_format() -> None:
    """``56% (28W/22L)`` — matches the in-game scoreboard's stat style."""
    from champ_assistant.profiling.profile import EnemyProfile
    profile = EnemyProfile(
        summoner_name="X",
        role_winrates={"MID": (28, 22)},
    )
    assert profile.role_summary("MID") == "56% (28W/22L)"


def test_enemy_profile_role_summary_none_for_unknown_role() -> None:
    from champ_assistant.profiling.profile import EnemyProfile
    profile = EnemyProfile(summoner_name="X", role_winrates={"TOP": (1, 0)})
    assert profile.role_summary("BOT") is None


def test_enemy_profile_has_data_includes_role_winrates() -> None:
    """Role winrate alone counts as "has data" — even before mastery
    or rank fetches complete."""
    from champ_assistant.profiling.profile import EnemyProfile
    profile = EnemyProfile(
        summoner_name="X",
        role_winrates={"TOP": (5, 3)},
    )
    assert profile.has_data is True


@pytest.mark.asyncio
async def test_summoner_404_returns_empty_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = RiotApiClient(
        "key", region="EUW",
        transport=httpx.MockTransport(handler),
    )
    service = ProfileService(client)
    profile = await service.fetch_by_puuid("Ghost")
    assert profile.has_data is False
    await client.aclose()
