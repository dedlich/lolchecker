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
async def test_summoner_by_puuid_parses_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Riot-Token"] == API_KEY
        assert "summoner/v4/summoners/by-puuid/" in str(request.url)
        return httpx.Response(200, json={
            "puuid": "PUUID", "id": "S-ID", "name": "Faker", "summonerLevel": 712,
        })

    client = _client(handler)
    info = await client.summoner_by_puuid("PUUID")
    assert info.puuid == "PUUID"
    assert info.level == 712
    await client.aclose()


@pytest.mark.asyncio
async def test_summoner_404_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(handler)
    with pytest.raises(RiotApiError):
        await client.summoner_by_puuid("MISSING")
    await client.aclose()


@pytest.mark.asyncio
async def test_invalid_key_raises_recognizable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    client = _client(handler)
    with pytest.raises(RiotApiError, match="invalid"):
        await client.summoner_by_puuid("PUUID")
    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limit_raises_recognizable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    client = _client(handler)
    with pytest.raises(RiotApiError, match="rate"):
        await client.summoner_by_puuid("PUUID")
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
async def test_recent_match_summaries_extracts_win_and_role() -> None:
    """Each summary captures both win + normalized role (TOP/JUNGLE/
    MID/BOT/SUPPORT) so streak + per-role winrate can both derive
    from a single fetch path."""
    match_outcomes = [("W", "TOP"), ("L", "TOP"), ("W", "MIDDLE")]
    match_ids = [f"EUW1_{i}" for i in range(len(match_outcomes))]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/by-puuid/" in url and "/ids" in url:
            return httpx.Response(200, json=match_ids)
        for i, mid in enumerate(match_ids):
            if mid in url:
                outcome, position = match_outcomes[i]
                return httpx.Response(200, json={
                    "info": {
                        "participants": [{
                            "puuid": "PUUID",
                            "win": outcome == "W",
                            "teamPosition": position,
                        }],
                    },
                })
        return httpx.Response(404)

    client = _client(handler)
    summaries = await client.recent_match_summaries("PUUID", count=3)
    assert len(summaries) == 3
    assert summaries[0] == {"win": True, "role": "TOP"}
    assert summaries[1] == {"win": False, "role": "TOP"}
    assert summaries[2] == {"win": True, "role": "MID"}  # MIDDLE → MID
    await client.aclose()


def test_streak_from_summaries_pure_aggregation() -> None:
    """Pure helper — testable without HTTP. W W W L W → streak=3."""
    from champ_assistant.profiling.riot_api import streak_from_summaries
    summaries = [
        {"win": True, "role": "MID"},
        {"win": True, "role": "MID"},
        {"win": True, "role": "MID"},
        {"win": False, "role": "MID"},
        {"win": True, "role": "MID"},
    ]
    wins, losses, streak = streak_from_summaries(summaries)
    assert wins == 4
    assert losses == 1
    assert streak == 3


def test_role_winrate_from_summaries_aggregates_by_role() -> None:
    from champ_assistant.profiling.riot_api import role_winrate_from_summaries
    summaries = [
        {"win": True, "role": "TOP"},
        {"win": True, "role": "TOP"},
        {"win": False, "role": "TOP"},
        {"win": True, "role": "MID"},
        {"win": False, "role": "MID"},
    ]
    by_role = role_winrate_from_summaries(summaries)
    assert by_role["TOP"] == (2, 1)
    assert by_role["MID"] == (1, 1)


def test_role_winrate_from_summaries_skips_missing_role() -> None:
    """Some matches have no teamPosition (ARAM, remake) — drop
    those rather than misclassify."""
    from champ_assistant.profiling.riot_api import role_winrate_from_summaries
    summaries = [
        {"win": True, "role": "TOP"},
        {"win": True, "role": None},
        {"win": False, "role": ""},
    ]
    by_role = role_winrate_from_summaries(summaries)
    assert by_role == {"TOP": (1, 0)}


@pytest.mark.asyncio
async def test_league_entries_by_puuid_parses_solo_and_flex() -> None:
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
        # Verify we hit the puuid form, not the deprecated by-summoner one.
        assert "/league/v4/entries/by-puuid/" in str(request.url)
        return httpx.Response(200, json=payload)

    client = _client(handler)
    entries = await client.league_entries_by_puuid("PUUID")
    assert len(entries) == 2
    solo = next(e for e in entries if e.queue_type == "RANKED_SOLO_5x5")
    assert solo.tier == "DIAMOND"
    assert solo.division == "II"
    assert solo.league_points == 24
    assert solo.games == 150
    assert solo.win_rate == pytest.approx(80 / 150)
    await client.aclose()


@pytest.mark.asyncio
async def test_league_entries_by_puuid_returns_empty_on_unranked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(handler)
    assert await client.league_entries_by_puuid("PUUID") == []
    await client.aclose()


def test_streak_for_loss_run_is_negative() -> None:
    """Three losses in a row → streak == -3."""
    from champ_assistant.profiling.riot_api import streak_from_summaries
    summaries = [{"win": False, "role": "MID"} for _ in range(3)]
    wins, losses, streak = streak_from_summaries(summaries)
    assert wins == 0
    assert losses == 3
    assert streak == -3


def test_set_credentials_updates_key_in_place() -> None:
    """v1.10.97: ``set_credentials`` swaps the X-Riot-Token header on the
    existing httpx.AsyncClient. Previously ``_on_settings_changed``
    rebuilt ProfileService → new RiotApiClient → new AsyncClient and
    leaked the old client's connection pool every save."""
    client = RiotApiClient(API_KEY, region="EUW")
    assert client._client.headers["X-Riot-Token"] == API_KEY
    assert client.region == "EUW"

    client.set_credentials(api_key="RGAPI-rotated", region="NA")
    assert client._client.headers["X-Riot-Token"] == "RGAPI-rotated"
    assert client.region == "NA"
    assert client._api_key == "RGAPI-rotated"


def test_set_credentials_with_no_region_leaves_region_unchanged() -> None:
    """API-key-only update path — Settings only flipped the LLM key,
    the user kept their region. Region must persist."""
    client = RiotApiClient(API_KEY, region="EUW")
    client.set_credentials(api_key="RGAPI-new")
    assert client.region == "EUW"
