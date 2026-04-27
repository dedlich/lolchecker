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
async def test_fetch_returns_full_profile() -> None:
    client = _client({
        "summoner/v4": {
            "puuid": "PUUID", "id": "S", "name": "Faker", "summonerLevel": 800,
        },
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
    profile = await service.fetch("Faker")
    assert profile.summoner_name == "Faker"
    assert profile.level == 800
    assert [c.champion_id for c in profile.top_champions] == [103, 64, 90]
    assert profile.wins == 2
    assert profile.losses == 0
    assert profile.streak == 2
    assert profile.win_rate == 1.0
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_caches_per_session() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        url = str(request.url)
        if "summoner/v4" in url:
            return httpx.Response(200, json={
                "puuid": "P", "id": "S", "name": "X", "summonerLevel": 10,
            })
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
    await service.fetch("X")
    first_calls = calls["n"]
    await service.fetch("X")  # should hit the cache
    assert calls["n"] == first_calls
    await client.aclose()


@pytest.mark.asyncio
async def test_disabled_when_no_key() -> None:
    client = RiotApiClient("", region="EUW")
    service = ProfileService(client)
    assert service.enabled is False
    await client.aclose()


@pytest.mark.asyncio
async def test_summoner_404_returns_empty_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = RiotApiClient(
        "key", region="EUW",
        transport=httpx.MockTransport(handler),
    )
    service = ProfileService(client)
    profile = await service.fetch("Ghost")
    assert profile.has_data is False
    await client.aclose()
