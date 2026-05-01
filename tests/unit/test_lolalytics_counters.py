"""Unit tests for data/lolalytics_counters.py."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from champ_assistant.data.lolalytics_counters import (
    LolalyticsCounterFetcher,
    _extract_matchup_rows,
    _parse_rows,
)


# ---------------------------------------------------------------------------
# Minimal Champion stub
# ---------------------------------------------------------------------------
@dataclass
class _Champ:
    id: int
    key: str
    name: str
    tags: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


_CHAMPS: dict[int, _Champ] = {
    266: _Champ(266, "Aatrox",   "Aatrox"),
    54:  _Champ(54,  "Malphite", "Malphite"),
    516: _Champ(516, "Ornn",     "Ornn"),
    51:  _Champ(51,  "Caitlyn",  "Caitlyn"),
    19:  _Champ(19,  "Warwick",  "Warwick"),
}


# ---------------------------------------------------------------------------
# _extract_matchup_rows — flexible field detection
# ---------------------------------------------------------------------------
def test_extract_direct_counters_key():
    payload = {"counters": [{"id": 266, "wr": 45.0, "games": 500}]}
    rows = _extract_matchup_rows(payload)
    assert len(rows) == 1
    assert rows[0]["id"] == 266


def test_extract_nested_data_counters():
    payload = {"data": {"counters": [{"id": 54, "wr": 43.0, "games": 300}]}}
    rows = _extract_matchup_rows(payload)
    assert len(rows) == 1
    assert rows[0]["id"] == 54


def test_extract_matchups_key():
    payload = {"matchups": [{"id": 516, "wr": 44.0, "games": 600}]}
    rows = _extract_matchup_rows(payload)
    assert len(rows) == 1


def test_extract_empty_when_no_known_key():
    payload = {"summary": {"wr": 51.0}, "unknown_field": []}
    rows = _extract_matchup_rows(payload)
    assert rows == []


def test_extract_skips_dict_without_id():
    payload = {"counters": [{"name": "foo", "wr": 40.0}]}
    rows = _extract_matchup_rows(payload)
    assert rows == []


# ---------------------------------------------------------------------------
# _parse_rows — row → CounterEntry
# ---------------------------------------------------------------------------
def test_parse_rows_basic():
    rows = [
        {"id": 266, "wr": 46.0, "games": 500},  # counter wr 54% → S
        {"id": 54,  "wr": 47.5, "games": 400},  # counter wr 52.5% → A
        {"id": 516, "wr": 50.0, "games": 1000}, # counter wr 50% → C
    ]
    entries = _parse_rows(rows, _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 3
    # Best counter first (lowest enemy wr = highest counter wr)
    assert entries[0].champion == "Aatrox"
    assert entries[0].tier == "S"
    assert entries[1].champion == "Malphite"


def test_parse_rows_filters_low_games():
    rows = [
        {"id": 266, "wr": 40.0, "games": 50},   # below MIN_GAMES — skipped
        {"id": 54,  "wr": 44.0, "games": 1000}, # kept
    ]
    entries = _parse_rows(rows, _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Malphite"


def test_parse_rows_skips_unknown_champion():
    rows = [
        {"id": 9999, "wr": 40.0, "games": 500},  # ID not in map
        {"id": 266,  "wr": 46.0, "games": 500},
    ]
    entries = _parse_rows(rows, _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Aatrox"


def test_parse_rows_respects_limit():
    rows = [{"id": cid, "wr": 44.0, "games": 500} for cid in _CHAMPS]
    entries = _parse_rows(rows, _CHAMPS, limit=3)  # type: ignore[arg-type]
    assert len(entries) == 3


def test_parse_rows_tier_s_plus():
    rows = [{"id": 266, "wr": 44.0, "games": 500}]  # 56% counter → S+
    entries = _parse_rows(rows, _CHAMPS)  # type: ignore[arg-type]
    assert entries[0].tier == "S+"


def test_parse_rows_empty():
    assert _parse_rows([], _CHAMPS) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LolalyticsCounterFetcher.fetch — mocked HTTP
# ---------------------------------------------------------------------------
def _make_fetcher(tmp_path: Path) -> LolalyticsCounterFetcher:
    return LolalyticsCounterFetcher(tmp_path / "cache", _CHAMPS, patch="15.8")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fetch_returns_entries(tmp_path: Path):
    # Enemy = "Aatrox" (id 266, in _CHAMPS). Response rows are champions
    # that counter Aatrox — ids 54 (Malphite) and 516 (Ornn), also in _CHAMPS.
    payload = {
        "counters": [
            {"id": 54,  "wr": 44.5, "games": 800},
            {"id": 516, "wr": 46.0, "games": 600},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)

    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Aatrox", "TOP")

    assert len(entries) == 2
    assert entries[0].champion == "Malphite"  # lower enemy wr → stronger counter


@pytest.mark.asyncio
async def test_fetch_caches_result(tmp_path: Path):
    payload = {"counters": [{"id": 54, "wr": 44.0, "games": 500}]}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)

    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        await fetcher.fetch("Aatrox", "TOP")
        # Second call should hit disk cache — no new network request.
        mock_client.get.reset_mock()
        result = await fetcher.fetch("Aatrox", "TOP")

    mock_client.get.assert_not_called()
    assert len(result) > 0


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_http_error(tmp_path: Path):
    import httpx
    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Darius", "TOP")

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_for_unknown_enemy(tmp_path: Path):
    fetcher = _make_fetcher(tmp_path)
    entries = await fetcher.fetch("UnknownChampion", "TOP")
    assert entries == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_no_matchup_data(tmp_path: Path):
    payload = {"summary": {"wr": 51.0}}  # no counter fields
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=payload)

    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Darius", "TOP")

    assert entries == []
