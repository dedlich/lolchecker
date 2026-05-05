"""Unit tests for data/refresh.py (SSR Qwik-state tier-list parser)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from champ_assistant.data.refresh import (
    REFRESH_TTL_S,
    _b36,
    _is_stale,
    _parse_qwik_state,
    _parse_tierlist_from_state,
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
    266:  _Champ(266,  "Aatrox",  "Aatrox"),
    103:  _Champ(103,  "Ahri",    "Ahri"),
    84:   _Champ(84,   "Akali",   "Akali"),
    51:   _Champ(51,   "Caitlyn", "Caitlyn"),
    19:   _Champ(19,   "Warwick", "Warwick"),
}


# ---------------------------------------------------------------------------
# Test helpers — build minimal Qwik objs arrays for tier-list pages
# ---------------------------------------------------------------------------

def _enc(n: int) -> str:
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    if n == 0:
        return "0"
    r = ""
    while n:
        r = chars[n % 36] + r
        n //= 36
    return r


def _build_tierlist_objs(
    champions: list[tuple[int, float, str]],
) -> list:
    """Build a minimal Qwik objs array for a tier-list page.

    Parameters
    ----------
    champions:
        List of (champion_id, win_rate, lane) tuples.

    Layout:
      [0]  gateway  {"K0pttOcjEAY": "1", "tPXLxeCn1mM": "2"}
      [1]  constants placeholder {}
      [2]  tierlist root  {"cid": "3"}
      [3]  cid dict  {str(id): ref, ...}
      [4+] stats dicts {"wr": wr_ref, "lane": lane_ref, "tier": "0", ...}
      then primitives (wr floats, lane strings)
    """
    # Reserve indices 0-3
    cid_dict: dict[str, str] = {}
    extra_objs: list = []
    base = 4

    for champ_id, wr, lane in champions:
        stats_idx = base + len(extra_objs)
        # The stats dict refs will point to values appended after it
        wr_ref   = _enc(stats_idx + 1)
        lane_ref = _enc(stats_idx + 2)
        stats = {"wr": wr_ref, "lane": lane_ref, "tier": "0", "rank": "0"}
        extra_objs.append(stats)      # stats_idx
        extra_objs.append(wr)         # wr_ref
        extra_objs.append(lane)       # lane_ref
        cid_dict[str(champ_id)] = _enc(stats_idx)

    objs: list = [
        {"K0pttOcjEAY": "1", "tPXLxeCn1mM": "2"},  # [0] gateway
        {},                                           # [1] constants
        {"cid": "3"},                                 # [2] tierlist root
        cid_dict,                                     # [3] cid dict
    ] + extra_objs

    return objs


def _wrap_html(objs: list) -> str:
    payload = json.dumps({"objs": objs})
    return f'<html><body><script type="qwik/json">{payload}</script></body></html>'


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
# _parse_tierlist_from_state — Qwik state → role entries
# ---------------------------------------------------------------------------
def test_parse_tierlist_basic():
    """Champions decoded by role with correct tier grades."""
    objs = _build_tierlist_objs([
        (266, 53.8, "top"),     # Aatrox S+
        (103, 51.5, "middle"),  # Ahri A
        (84,  50.8, "middle"),  # Akali B
        (51,  54.0, "bottom"),  # Caitlyn S+
    ])
    role_entries = _parse_tierlist_from_state(objs, _CHAMPS)  # type: ignore[arg-type]

    assert set(role_entries.keys()) >= {"TOP", "MID", "BOT"}
    assert role_entries["TOP"][0].champion == "Aatrox"
    assert role_entries["TOP"][0].tier == "S+"
    assert role_entries["BOT"][0].champion == "Caitlyn"

    mid_champs = {e.champion for e in role_entries["MID"]}
    assert "Ahri" in mid_champs
    assert "Akali" in mid_champs


def test_parse_tierlist_sorts_by_tier_then_wr():
    """Within same role, S+ comes before S, A, B, etc."""
    objs = _build_tierlist_objs([
        (84,  50.5, "jungle"),   # B
        (19,  52.5, "jungle"),   # S
        (266, 54.0, "jungle"),   # S+
    ])
    entries = _parse_tierlist_from_state(objs, _CHAMPS)["JUNGLE"]  # type: ignore[arg-type]
    tiers = [e.tier for e in entries]
    # S+ must appear before S, S before B
    assert tiers.index("S+") < tiers.index("S")
    assert tiers.index("S") < tiers.index("B")


def test_parse_tierlist_skips_unknown_champions():
    objs = _build_tierlist_objs([
        (9999, 53.0, "top"),   # not in _CHAMPS
        (266,  52.0, "top"),   # known
    ])
    entries = _parse_tierlist_from_state(objs, _CHAMPS)  # type: ignore[arg-type]
    assert len(entries.get("TOP", [])) == 1
    assert entries["TOP"][0].champion == "Aatrox"


def test_parse_tierlist_raises_when_gateway_missing():
    with pytest.raises(ValueError, match="gateway"):
        _parse_tierlist_from_state([{"unrelated": "data"}], _CHAMPS)  # type: ignore[arg-type]


def test_parse_tierlist_empty_champions():
    objs = _build_tierlist_objs([])
    result = _parse_tierlist_from_state(objs, _CHAMPS)  # type: ignore[arg-type]
    assert result == {}


# ---------------------------------------------------------------------------
# maybe_refresh — integration: mocked HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_maybe_refresh_updates_tiers(tmp_path: Path):
    """Successful page fetch → returns TierList and writes tiers.json."""
    objs = _build_tierlist_objs([
        (266, 53.8, "top"),
        (103, 51.0, "middle"),
        (51,  54.0, "bottom"),
    ])
    html = _wrap_html(objs)

    with patch(
        "champ_assistant.data.refresh._fetch_tierlist_page",
        new=AsyncMock(return_value=html),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]

    assert result is not None
    assert "TOP" in result.tiers
    assert result.tiers["TOP"][0].champion == "Aatrox"

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
async def test_maybe_refresh_returns_none_on_empty_response(tmp_path: Path):
    """Empty HTTP response → returns None, no file written."""
    with patch(
        "champ_assistant.data.refresh._fetch_tierlist_page",
        new=AsyncMock(return_value=""),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]
    assert result is None
    assert not (tmp_path / "tiers.json").exists()


@pytest.mark.asyncio
async def test_maybe_refresh_returns_none_on_parse_failure(tmp_path: Path):
    """Malformed HTML (no Qwik state) → returns None."""
    with patch(
        "champ_assistant.data.refresh._fetch_tierlist_page",
        new=AsyncMock(return_value="<html>no qwik here</html>"),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_maybe_refresh_returns_none_when_no_champion_map(tmp_path: Path):
    result = await maybe_refresh(tmp_path, {}, force=True)
    assert result is None


@pytest.mark.asyncio
async def test_maybe_refresh_force_ignores_fresh_stamp(tmp_path: Path):
    """force=True bypasses TTL even with a fresh stamp."""
    (tmp_path / ".tiers_refreshed_at").write_text(str(time.time()), encoding="utf-8")

    objs = _build_tierlist_objs([(266, 53.0, "top")])
    html = _wrap_html(objs)

    with patch(
        "champ_assistant.data.refresh._fetch_tierlist_page",
        new=AsyncMock(return_value=html),
    ):
        result = await maybe_refresh(tmp_path, _CHAMPS, force=True)  # type: ignore[arg-type]

    assert result is not None
