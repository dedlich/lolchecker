"""Unit tests for data/refresh.py."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from champ_assistant.data.refresh import (
    REFRESH_TTL_S,
    _is_stale,
    _parse_entries,
    _wr_to_tier,
    maybe_refresh,
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
    266:  _Champ(266,  "Aatrox",    "Aatrox"),
    103:  _Champ(103,  "Ahri",      "Ahri"),
    84:   _Champ(84,   "Akali",     "Akali"),
    51:   _Champ(51,   "Caitlyn",   "Caitlyn"),
    19:   _Champ(19,   "Warwick",   "Warwick"),
}


# ---------------------------------------------------------------------------
# _wr_to_tier — win-rate bucketing
# ---------------------------------------------------------------------------
def test_wr_to_tier_s_plus():
    assert _wr_to_tier(54.0) == "S+"


def test_wr_to_tier_s():
    assert _wr_to_tier(52.5) == "S"


def test_wr_to_tier_a():
    assert _wr_to_tier(51.3) == "A"


def test_wr_to_tier_b():
    assert _wr_to_tier(50.3) == "B"


def test_wr_to_tier_c():
    assert _wr_to_tier(49.0) == "C"


def test_wr_to_tier_d():
    assert _wr_to_tier(44.0) == "D"


# ---------------------------------------------------------------------------
# _parse_entries — raw row → TierEntry list
# ---------------------------------------------------------------------------
def test_parse_entries_basic():
    rows = [
        {"id": 266, "wr": 53.8},  # S+
        {"id": 103, "wr": 51.5},  # A
        {"id": 84,  "wr": 49.0},  # C
    ]
    entries = _parse_entries(rows, "TOP", _CHAMPS)  # type: ignore[arg-type]
    keys = [e.champion for e in entries]
    assert "Aatrox" in keys
    assert "Ahri" in keys
    assert "Akali" in keys
    # S+ should sort first
    assert entries[0].tier == "S+"


def test_parse_entries_skips_unknown_champion():
    rows = [{"id": 9999, "wr": 53.0}, {"id": 266, "wr": 52.5}]
    entries = _parse_entries(rows, "TOP", _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Aatrox"


def test_parse_entries_skips_missing_wr():
    rows = [{"id": 266}, {"id": 103, "wr": 51.0}]
    entries = _parse_entries(rows, "TOP", _CHAMPS)  # type: ignore[arg-type]
    assert len(entries) == 1
    assert entries[0].champion == "Ahri"


def test_parse_entries_empty_rows():
    assert _parse_entries([], "TOP", _CHAMPS) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _is_stale — TTL check
# ---------------------------------------------------------------------------
def test_is_stale_missing_stamp(tmp_path: Path):
    assert _is_stale(tmp_path) is True


def test_is_stale_fresh_stamp(tmp_path: Path):
    (tmp_path / ".tiers_refreshed_at").write_text(str(time.time()), encoding="utf-8")
    assert _is_stale(tmp_path) is False


def test_is_stale_old_stamp(tmp_path: Path):
    old_ts = time.time() - REFRESH_TTL_S - 60
    (tmp_path / ".tiers_refreshed_at").write_text(str(old_ts), encoding="utf-8")
    assert _is_stale(tmp_path) is True


# ---------------------------------------------------------------------------
# maybe_refresh — integration: mocked HTTP
# ---------------------------------------------------------------------------
def _mock_payload(ids_wrs: list[tuple[int, float]]) -> dict:
    return {
        "patch": "current",
        "data": [{"id": i, "n": "X", "wr": wr, "pr": 3.0, "br": 5.0}
                 for i, wr in ids_wrs],
    }


@pytest.mark.asyncio
async def test_maybe_refresh_updates_tiers(tmp_path: Path):
    """Successful fetch → returns TierList and writes tiers.json."""
    lane_data = {
        "top":     _mock_payload([(266, 53.8), (103, 51.0)]),
        "jungle":  _mock_payload([(19, 52.1)]),
        "mid":     _mock_payload([(84, 50.8)]),
        "adc":     _mock_payload([(51, 54.0)]),
        "support": _mock_payload([]),
    }

    async def fake_fetch(client, lane, *, tier, region):
        return lane_data.get(lane, {}).get("data", [])

    with patch(
        "champ_assistant.data.refresh._fetch_lane",
        new=AsyncMock(side_effect=fake_fetch),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]

    assert result is not None
    # TOP should have both entries
    assert len(result.tiers.get("TOP", [])) == 2
    # tiers.json was written
    tiers_json = json.loads((tmp_path / "tiers.json").read_text())
    assert "TOP" in tiers_json["tiers"]
    assert tiers_json["tiers"]["TOP"][0]["champion"] == "Aatrox"
    assert tiers_json["tiers"]["TOP"][0]["tier"] == "S+"


@pytest.mark.asyncio
async def test_maybe_refresh_returns_none_when_fresh(tmp_path: Path):
    """Fresh stamp → skip without any HTTP calls."""
    (tmp_path / ".tiers_refreshed_at").write_text(str(time.time()), encoding="utf-8")
    result = await maybe_refresh(tmp_path, _CHAMPS)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_maybe_refresh_returns_none_when_all_lanes_empty(tmp_path: Path):
    """All lanes return empty → nothing to write, returns None."""
    with patch(
        "champ_assistant.data.refresh._fetch_lane",
        new=AsyncMock(return_value=[]),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]
    assert result is None
    assert not (tmp_path / "tiers.json").exists()


@pytest.mark.asyncio
async def test_maybe_refresh_returns_none_when_no_champion_map(tmp_path: Path):
    result = await maybe_refresh(tmp_path, {}, force=True)
    assert result is None


@pytest.mark.asyncio
async def test_maybe_refresh_force_ignores_fresh_stamp(tmp_path: Path):
    """force=True bypasses TTL even with a fresh stamp."""
    (tmp_path / ".tiers_refreshed_at").write_text(str(time.time()), encoding="utf-8")
    lane_payload = _mock_payload([(266, 53.0)])

    async def fake_fetch(client, lane, *, tier, region):
        return lane_payload["data"]

    with patch(
        "champ_assistant.data.refresh._fetch_lane",
        new=AsyncMock(side_effect=fake_fetch),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]

    assert result is not None
