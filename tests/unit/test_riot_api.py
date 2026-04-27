"""Tests for the Riot Web API client."""
from __future__ import annotations

import httpx
import pytest

from champ_assistant.profiling.riot_api import RiotApiClient, RiotApiError

API_KEY = "RGAPI-test-1234"


def _client(handler) -> RiotApiClient:
    return RiotApiClient(
        API_KEY,
        region="EUW",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_summoner_by_name_parses_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Riot-Token"] == API_KEY
        assert "summoner/v4" in str(request.url)
        return httpx.Response(200, json={
            "puuid": "PUUID", "id": "S-ID", "name": "Faker", "summonerLevel": 712,
        })

    client = _client(handler)
    info = await client.summoner_by_name("Faker")
    assert info.puuid == "PUUID"
    assert info.level == 712
    await client.aclose()


@pytest.mark.asyncio
async def test_summoner_404_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(RiotApiError):
        await client.summoner_by_name("Nope")
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_key_raises_recognizable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client = _client(handler)
    with pytest.raises(RiotApiError, match="invalid"):
        await client.summoner_by_name("X")
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_raises_recognizable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(handler)
    with pytest.raises(RiotApiError, match="rate"):
        await client.summoner_by_name("X")
    await client.aclose()


@pytest.mark.asyncio
async def test_top_mastery_returns_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"championId": 103, "championPoints": 500_000, "championLevel": 7},
            {"championId": 64, "championPoints": 250_000, "championLevel": 6},
        ])

    client = _client(handler)
    mastery = await client.top_mastery("PUUID", count=2)
    assert [m.champion_id for m in mastery] == [103, 64]
    assert mastery[0].points == 500_000
    await client.aclose()


@pytest.mark.asyncio
async def test_streak_computes_signed_streak() -> None:
    """Latest 5 outcomes: W W W L W → 3-game win streak from the front."""
    match_outcomes = ["W", "W", "W", "L", "W"]
    match_ids = [f"EUW1_{i}" for i in range(len(match_outcomes))]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/by-puuid/" in url and "/ids" in url:
            return httpx.Response(200, json=match_ids)
        # Match details endpoint — figure out which one and return win/loss.
        for i, mid in enumerate(match_ids):
            if mid in url:
                won = match_outcomes[i] == "W"
                return httpx.Response(200, json={
                    "info": {
                        "participants": [
                            {"puuid": "PUUID", "win": won},
                        ],
                    },
                })
        return httpx.Response(404)

    client = _client(handler)
    wins, losses, streak = await client.win_loss_streak("PUUID", count=5)
    assert wins == 4
    assert losses == 1
    assert streak == 3  # WWW from the front
    await client.aclose()


@pytest.mark.asyncio
async def test_league_entries_parses_solo_and_flex() -> None:
    payload = [
        {
            "queueType": "RANKED_SOLO_5x5",
            "tier": "DIAMOND", "rank": "II",
            "leaguePoints": 24, "wins": 80, "losses": 70,
        },
        {
            "queueType": "RANKED_FLEX_SR",
            "tier": "PLATINUM", "rank": "I",
            "leaguePoints": 88, "wins": 12, "losses": 10,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _client(handler)
    entries = await client.league_entries("S-1234")
    assert len(entries) == 2
    solo = next(e for e in entries if e.queue_type == "RANKED_SOLO_5x5")
    assert solo.tier == "DIAMOND"
    assert solo.division == "II"
    assert solo.league_points == 24
    assert solo.games == 150
    assert solo.win_rate == pytest.approx(80 / 150)
    await client.aclose()


@pytest.mark.asyncio
async def test_league_entries_returns_empty_on_unranked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(handler)
    assert await client.league_entries("S-x") == []
    await client.aclose()


@pytest.mark.asyncio
async def test_streak_for_loss_run_is_negative() -> None:
    match_ids = ["L1", "L2", "L3"]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/ids" in url:
            return httpx.Response(200, json=match_ids)
        return httpx.Response(200, json={
            "info": {"participants": [{"puuid": "PUUID", "win": False}]},
        })

    client = _client(handler)
    wins, losses, streak = await client.win_loss_streak("PUUID", count=3)
    assert wins == 0
    assert losses == 3
    assert streak == -3
    await client.aclose()
