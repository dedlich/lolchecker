"""Tests for the Data Dragon client (HTTP + diskcache)."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from champ_assistant.data.datadragon import (
    CHAMPIONS_URL_TEMPLATE,
    VERSIONS_URL,
    DataDragon,
    DataDragonError,
)


SAMPLE_CHAMPIONS_PAYLOAD = {
    "type": "champion",
    "version": "14.8.1",
    "data": {
        "Aatrox": {"id": "Aatrox", "key": "266", "name": "Aatrox", "tags": ["Fighter"]},
        "Garen": {
            "id": "Garen",
            "key": "86",
            "name": "Garen",
            "tags": ["Fighter", "Tank"],
        },
        "MissFortune": {
            "id": "MissFortune",
            "key": "21",
            "name": "Miss Fortune",
            "tags": ["Marksman"],
        },
    },
}


# ---------------------------------------------------------------------------
# fetch_latest_patch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_returns_first(tmp_path: Path) -> None:
    respx.get(VERSIONS_URL).mock(
        return_value=httpx.Response(200, json=["14.8.1", "14.7.1", "14.6.1"])
    )
    async with DataDragon(tmp_path / "cache") as dd:
        patch = await dd.fetch_latest_patch()
    assert patch == "14.8.1"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_uses_cache(tmp_path: Path) -> None:
    route = respx.get(VERSIONS_URL).mock(
        return_value=httpx.Response(200, json=["14.8.1"])
    )
    async with DataDragon(tmp_path / "cache") as dd:
        await dd.fetch_latest_patch()
        await dd.fetch_latest_patch()
        await dd.fetch_latest_patch()
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_persists_cache_across_instances(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    route = respx.get(VERSIONS_URL).mock(
        return_value=httpx.Response(200, json=["14.8.1"])
    )
    async with DataDragon(cache) as dd:
        await dd.fetch_latest_patch()
    async with DataDragon(cache) as dd:
        patch = await dd.fetch_latest_patch()
    assert patch == "14.8.1"
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_raises_on_http_error(tmp_path: Path) -> None:
    respx.get(VERSIONS_URL).mock(return_value=httpx.Response(500))
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError, match="versions fetch failed"):
            await dd.fetch_latest_patch()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_raises_on_network_error(tmp_path: Path) -> None:
    respx.get(VERSIONS_URL).mock(side_effect=httpx.ConnectError("boom"))
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError):
            await dd.fetch_latest_patch()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_raises_on_empty_list(tmp_path: Path) -> None:
    respx.get(VERSIONS_URL).mock(return_value=httpx.Response(200, json=[]))
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError, match="non-empty list"):
            await dd.fetch_latest_patch()


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_patch_raises_when_not_a_list(tmp_path: Path) -> None:
    respx.get(VERSIONS_URL).mock(return_value=httpx.Response(200, json={"oops": True}))
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError):
            await dd.fetch_latest_patch()


# ---------------------------------------------------------------------------
# fetch_champions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_parses_riot_naming(tmp_path: Path) -> None:
    respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json=SAMPLE_CHAMPIONS_PAYLOAD)
    )
    async with DataDragon(tmp_path / "cache") as dd:
        champs = await dd.fetch_champions("14.8.1")
    assert 86 in champs
    garen = champs[86]
    assert garen.key == "Garen"  # Riot's "id" → our key
    assert garen.id == 86  # Riot's "key" → our id
    assert garen.name == "Garen"
    assert garen.tags == ["Fighter", "Tank"]
    assert champs[21].name == "Miss Fortune"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_uses_cache(tmp_path: Path) -> None:
    route = respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json=SAMPLE_CHAMPIONS_PAYLOAD)
    )
    async with DataDragon(tmp_path / "cache") as dd:
        await dd.fetch_champions("14.8.1")
        await dd.fetch_champions("14.8.1")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_separate_cache_per_patch(tmp_path: Path) -> None:
    r1 = respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json=SAMPLE_CHAMPIONS_PAYLOAD)
    )
    r2 = respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.7.1")).mock(
        return_value=httpx.Response(200, json=SAMPLE_CHAMPIONS_PAYLOAD)
    )
    async with DataDragon(tmp_path / "cache") as dd:
        await dd.fetch_champions("14.8.1")
        await dd.fetch_champions("14.7.1")
    assert r1.call_count == 1
    assert r2.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_skips_unparseable_entries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad_payload = {
        "type": "champion",
        "version": "14.8.1",
        "data": {
            "Garen": {"id": "Garen", "key": "86", "name": "Garen", "tags": ["Fighter"]},
            "Bad": {"id": "Bad", "key": "not-an-int", "name": "Bad"},
        },
    }
    respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json=bad_payload)
    )
    import logging
    async with DataDragon(tmp_path / "cache") as dd:
        with caplog.at_level(logging.WARNING, logger="champ_assistant.data.datadragon"):
            champs = await dd.fetch_champions("14.8.1")
    assert 86 in champs
    assert len(champs) == 1
    assert any(rec.message == "ddragon_champion_skipped" for rec in caplog.records)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_all_unparseable_raises(tmp_path: Path) -> None:
    bad_payload = {
        "type": "champion",
        "version": "14.8.1",
        "data": {"Bad": {"id": "Bad", "key": "x", "name": "Bad"}},
    }
    respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json=bad_payload)
    )
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError, match="no parseable champions"):
            await dd.fetch_champions("14.8.1")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_missing_data_key_raises(tmp_path: Path) -> None:
    respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(200, json={"oops": True})
    )
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError, match="missing 'data'"):
            await dd.fetch_champions("14.8.1")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_champions_http_error(tmp_path: Path) -> None:
    respx.get(CHAMPIONS_URL_TEMPLATE.format(patch="14.8.1")).mock(
        return_value=httpx.Response(404)
    )
    async with DataDragon(tmp_path / "cache") as dd:
        with pytest.raises(DataDragonError, match="champions fetch failed"):
            await dd.fetch_champions("14.8.1")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outside_context_manager_raises(tmp_path: Path) -> None:
    dd = DataDragon(tmp_path / "cache")
    with pytest.raises(RuntimeError, match="async context manager"):
        await dd.fetch_latest_patch()
    dd.cache.close()
