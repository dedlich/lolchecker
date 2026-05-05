"""Unit tests for data/lolalytics_counters.py (SSR Qwik-state parser)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from champ_assistant.data.lolalytics_counters import (
    LolalyticsCounterFetcher,
    _b36,
    _extract_counters,
    _parse_qwik_state,
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
# Test helpers — build minimal Qwik objs arrays
# ---------------------------------------------------------------------------

def _enc(n: int) -> str:
    """Encode an integer to a base-36 Qwik reference string."""
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    r = ""
    while n:
        r = chars[n % 36] + r
        n //= 36
    return r


def _build_objs(
    lane: str,
    rows: list[tuple[int, float, int]],
) -> list:
    """Build a minimal Qwik ``objs`` array containing counter data for *lane*.

    Layout:
      [0]  gateway dict  {"K0pttOcjEAY": "1", "tPXLxeCn1mM": "2"}
      [1]  constants placeholder {}
      [2]  data dict     {"enemy": "3", "enemy_h": "4"}
      [3]  enemy dict    {lane: "5"}
      [4]  headers list  ["6","7","8","9","a","b"]
      [5]  header "id"
      [6]  header "wr"
      [7]  header "d1"
      [8]  header "d2"
      [9]  header "pr"
      [10] header "n"
      [11] lane row-ref list  ["c", "d", ...]
      [12+] row lists [[id_ref, wr_ref, d1_ref, d2_ref, pr_ref, n_ref], ...]
      then the primitive values for each row's id/wr/n
    """
    objs: list = [None] * 12  # reserve indices 0..11

    # Fixed references
    objs[0] = {"K0pttOcjEAY": "1", "tPXLxeCn1mM": "2"}
    objs[1] = {}
    objs[2] = {"enemy": "3", "enemy_h": "4"}
    objs[3] = {lane: "b"}          # "b" = 11 = lane list index
    objs[4] = ["5", "6", "7", "8", "9", "a"]  # header refs (indices 5-10)
    objs[5] = "id"
    objs[6] = "wr"
    objs[7] = "d1"
    objs[8] = "d2"
    objs[9] = "pr"
    objs[10] = "n"

    # Build row data starting at index 12
    row_ref_list: list[str] = []
    idx = 12
    for champ_id, focal_wr, games in rows:
        # Each row is a list of 6 value refs
        id_ref  = _enc(idx);     objs.append(champ_id);   idx += 1
        wr_ref  = _enc(idx);     objs.append(focal_wr);   idx += 1
        d1_ref  = _enc(idx);     objs.append(0.0);        idx += 1
        d2_ref  = _enc(idx);     objs.append(0.0);        idx += 1
        pr_ref  = _enc(idx);     objs.append(1.0);        idx += 1
        n_ref   = _enc(idx);     objs.append(games);      idx += 1
        row_ref = _enc(idx);     objs.append([id_ref, wr_ref, d1_ref, d2_ref, pr_ref, n_ref]); idx += 1
        row_ref_list.append(row_ref)

    objs[11] = row_ref_list  # "b" = 11
    return objs


def _wrap_html(objs: list) -> str:
    payload = json.dumps({"objs": objs})
    return f'<html><body><script type="qwik/json">{payload}</script></body></html>'


# ---------------------------------------------------------------------------
# _b36 — base-36 decoder
# ---------------------------------------------------------------------------
def test_b36_single_digit():
    assert _b36("0") == 0
    assert _b36("9") == 9


def test_b36_letters():
    assert _b36("a") == 10
    assert _b36("z") == 35


def test_b36_multi():
    # "10" in base-36 = 1*36 + 0 = 36
    assert _b36("10") == 36


def test_b36_invalid_returns_none():
    assert _b36("!X") is None


# ---------------------------------------------------------------------------
# _parse_qwik_state
# ---------------------------------------------------------------------------
def test_parse_qwik_state_extracts_objs():
    objs = [1, "hello", {"key": "val"}]
    html = f'<script type="qwik/json">{{"objs": {json.dumps(objs)}}}</script>'
    result = _parse_qwik_state(html)
    assert result == objs


def test_parse_qwik_state_raises_when_missing():
    with pytest.raises(ValueError, match="qwik/json"):
        _parse_qwik_state("<html></html>")


# ---------------------------------------------------------------------------
# _extract_counters — end-to-end with minimal Qwik state
# ---------------------------------------------------------------------------
def test_extract_basic_counters():
    """Two opponents with enough games → two CounterEntry objects, best first."""
    objs = _build_objs("top", [
        (266, 46.0, 500),  # Aatrox: Darius WR 46% → counter_wr 54% → S
        (54,  47.5, 400),  # Malphite: Darius WR 47.5% → counter_wr 52.5% → A
    ])
    entries = _extract_counters(objs, "top", _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 2
    assert entries[0].champion == "Aatrox"   # lower focal_wr = better counter
    assert entries[0].tier == "S"
    assert entries[1].champion == "Malphite"
    assert entries[1].tier in ("A", "B")


def test_extract_filters_low_games():
    """Opponents with fewer than MIN_GAMES are excluded."""
    objs = _build_objs("top", [
        (266, 40.0, 50),    # too few games — skip
        (54,  44.0, 1000),  # enough games — keep
    ])
    entries = _extract_counters(objs, "top", _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Malphite"


def test_extract_skips_unknown_champion():
    """Champion IDs not in champ_by_id are ignored."""
    objs = _build_objs("top", [
        (9999, 40.0, 500),  # unknown
        (266,  46.0, 500),  # known
    ])
    entries = _extract_counters(objs, "top", _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Aatrox"


def test_extract_respects_limit():
    objs = _build_objs("top", [
        (cid, 44.0, 500) for cid in _CHAMPS
    ])
    entries = _extract_counters(objs, "top", _CHAMPS, limit=2)  # type: ignore[arg-type]
    assert len(entries) == 2


def test_extract_tier_s_plus():
    """focal_wr 44% → counter_wr 56% → S+."""
    objs = _build_objs("jungle", [(266, 44.0, 500)])
    entries = _extract_counters(objs, "jungle", _CHAMPS)  # type: ignore[arg-type]
    assert entries[0].tier == "S+"


def test_extract_wrong_lane_raises():
    """Requesting a lane not in the enemy container raises ValueError."""
    objs = _build_objs("top", [(266, 46.0, 500)])
    with pytest.raises((ValueError, KeyError)):
        _extract_counters(objs, "middle", _CHAMPS)  # type: ignore[arg-type]


def test_extract_empty_rows():
    objs = _build_objs("support", [])
    entries = _extract_counters(objs, "support", _CHAMPS)  # type: ignore[arg-type]
    assert entries == []


def test_extract_missing_gateway_raises():
    objs = [{"some_other_key": "value"}]
    with pytest.raises(ValueError, match="gateway"):
        _extract_counters(objs, "top", _CHAMPS)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LolalyticsCounterFetcher.fetch — mocked HTTP
# ---------------------------------------------------------------------------
def _make_fetcher(tmp_path: Path) -> LolalyticsCounterFetcher:
    return LolalyticsCounterFetcher(tmp_path / "cache", _CHAMPS, patch="15.8")  # type: ignore[arg-type]


def _make_mock_html_response(objs: list) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = _wrap_html(objs)
    return mock_resp


@pytest.mark.asyncio
async def test_fetch_returns_entries(tmp_path: Path):
    """fetch() parses HTML Qwik state and returns counter entries."""
    objs = _build_objs("top", [
        (54,  44.5, 800),   # Malphite counter_wr 55.5% → S
        (516, 46.0, 600),   # Ornn counter_wr 54% → S
    ])
    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_mock_html_response(objs))
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Aatrox", "TOP")

    assert len(entries) == 2
    assert entries[0].champion == "Malphite"  # lower focal_wr → better counter


@pytest.mark.asyncio
async def test_fetch_caches_result(tmp_path: Path):
    """Second call with same args returns cached result without hitting network."""
    objs = _build_objs("top", [(54, 44.0, 500)])
    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_make_mock_html_response(objs))
        mock_cls.return_value = mock_client

        await fetcher.fetch("Aatrox", "TOP")
        mock_client.get.reset_mock()
        result = await fetcher.fetch("Aatrox", "TOP")

    mock_client.get.assert_not_called()
    assert len(result) > 0


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_http_error(tmp_path: Path):
    """HTTP errors are swallowed and return an empty list."""
    import httpx as _httpx
    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_httpx.ConnectError("timeout"))
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Darius", "TOP")

    assert entries == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_parse_fails(tmp_path: Path):
    """Malformed HTML (no qwik/json block) returns an empty list."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "<html><body>not a qwik page</body></html>"

    fetcher = _make_fetcher(tmp_path)
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_cls.return_value = mock_client

        entries = await fetcher.fetch("Darius", "TOP")

    assert entries == []
