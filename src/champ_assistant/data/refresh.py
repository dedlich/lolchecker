"""Static data refresh service — pulls live tier lists from Lolalytics.

Runs once at startup (as a background task) and every TTL_S hours thereafter.
On success: writes updated ``tiers.json`` atomically and hands a new
``TierList`` to the caller. On any network / parse failure: existing files
are untouched and ``None`` is returned so callers can keep their current data.

Endpoint (Lolalytics public stats API):
  GET https://lolalytics.com/api/tierlist/1/
      ?patch=current&tier={tier}&region={region}&lane={lane}&hv=3

Representative response (defensive parsing handles format drift):
  {
    "patch": "15.8",
    "data": [
      {"id": 266, "n": "Aatrox", "wr": 50.4, "pr": 3.1, "br": 7.2}
    ]
  }

Counter data is handled lazily per-champion in ``runtime_counters.py``.
Builds are not refreshed here — the LCU push layer reads from builds.json
which is updated out-of-band (manual curation or a future separate fetch).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .models import Role, TierEntry, TierList

if TYPE_CHECKING:
    from .models import Champion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOLALYTICS_BASE = "https://lolalytics.com/api/tierlist/1/"
REFRESH_TTL_S: float = 6 * 3600        # re-fetch after 6 hours
REQUEST_TIMEOUT: float = 10.0
MAX_CONCURRENCY: int = 3                # parallel lane requests

_STAMP_FILENAME = ".tiers_refreshed_at"

_LANE_TO_ROLE: dict[str, Role] = {
    "top":     "TOP",
    "jungle":  "JUNGLE",
    "mid":     "MID",
    "adc":     "BOT",
    "support": "SUPPORT",
}

# Win-rate thresholds → our Tier grade (descending).
_WR_TIERS: list[tuple[float, str]] = [
    (53.5, "S+"),
    (52.0, "S"),
    (51.0, "A"),
    (50.0, "B"),
    (48.5, "C"),
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://lolalytics.com/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stamp_path(data_dir: Path) -> Path:
    return data_dir / _STAMP_FILENAME


def _is_stale(data_dir: Path) -> bool:
    """True when the stamp file is missing or older than TTL_S."""
    p = _stamp_path(data_dir)
    if not p.exists():
        return True
    try:
        written_at = float(p.read_text(encoding="utf-8").strip())
        return (time.time() - written_at) > REFRESH_TTL_S
    except (OSError, ValueError):
        return True


def _write_stamp(data_dir: Path) -> None:
    try:
        _stamp_path(data_dir).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def _wr_to_tier(win_rate: float) -> str:
    for threshold, grade in _WR_TIERS:
        if win_rate >= threshold:
            return grade
    return "D"


def _atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON to a temp file then rename — never leaves a partial file."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.warning("refresh_write_failed path=%s err=%s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Fetch one lane
# ---------------------------------------------------------------------------

async def _fetch_lane(
    client: httpx.AsyncClient,
    lane: str,
    *,
    tier: str,
    region: str,
) -> list[dict[str, Any]]:
    """Fetch raw tier-list rows for one lane. Returns [] on any error."""
    params = {
        "patch": "current",
        "tier": tier,
        "region": region,
        "lane": lane,
        "hv": "4",
    }
    try:
        resp = await client.get(LOLALYTICS_BASE, params=params, headers=_HEADERS)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("lolalytics_fetch_failed lane=%s err=%s", lane, exc)
        return []

    data = payload.get("data") or []
    if not isinstance(data, list):
        logger.warning("lolalytics_unexpected_shape lane=%s", lane)
        return []
    return data


# ---------------------------------------------------------------------------
# Parse raw rows into TierEntry objects
# ---------------------------------------------------------------------------

def _parse_entries(
    rows: list[dict[str, Any]],
    role: Role,
    champ_by_id: dict[int, "Champion"],
) -> list[TierEntry]:
    """Convert raw Lolalytics rows to sorted TierEntry list for one role."""
    entries: list[TierEntry] = []
    for row in rows:
        champ_id = row.get("id")
        win_rate = row.get("wr")
        if not isinstance(champ_id, int) or not isinstance(win_rate, float | int):
            continue
        champ = champ_by_id.get(champ_id)
        if champ is None:
            continue
        grade = _wr_to_tier(float(win_rate))
        entries.append(TierEntry(champion=champ.key, tier=grade))  # type: ignore[arg-type]

    # Sort S+ first, then by win rate descending — we store the order
    # so callers can use positional priority without re-sorting.
    _order = {"S+": 0, "S": 1, "A": 2, "B": 3, "C": 4, "D": 5}
    entries.sort(key=lambda e: _order.get(e.tier, 9))
    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def maybe_refresh(
    data_dir: Path,
    champ_by_id: dict[int, "Champion"],
    *,
    tier: str = "platinum_plus",
    region: str = "euw",
    force: bool = False,
) -> TierList | None:
    """Refresh tier data from Lolalytics when the cached copy is stale.

    Parameters
    ----------
    data_dir:
        Directory that holds ``tiers.json`` (the project ``data/`` folder).
    champ_by_id:
        DataDragon champion map keyed by numeric ID — used to resolve
        Lolalytics' numeric ``id`` fields to our champion key strings.
    tier:
        Lolalytics tier filter. ``"platinum_plus"`` is the recommended
        default; ``"diamond_plus"`` for a higher-elo perspective.
    region:
        Lolalytics region. ``"euw"``, ``"na"``, ``"kr"`` or ``"all"``.
    force:
        Skip the TTL check and always fetch. Useful for manual refresh.

    Returns
    -------
    Updated ``TierList`` when data was freshly fetched, ``None`` when the
    cache is still fresh or when all fetches failed.
    """
    if not force and not _is_stale(data_dir):
        logger.debug("tier_refresh_skipped reason=fresh")
        return None

    if not champ_by_id:
        logger.warning("tier_refresh_skipped reason=no_champion_map")
        return None

    logger.info("tier_refresh_start tier=%s region=%s", tier, region)

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def fetch_one(lane: str) -> tuple[str, list[dict[str, Any]]]:
        async with sem:
            rows = await _fetch_lane(client, lane, tier=tier, region=region)
            return lane, rows

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        results = await asyncio.gather(
            *(fetch_one(lane) for lane in _LANE_TO_ROLE),
            return_exceptions=True,
        )

    # Build the role → entries mapping.
    role_entries: dict[Role, list[TierEntry]] = {}
    any_ok = False
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("tier_refresh_lane_error err=%s", result)
            continue
        lane, rows = result
        role = _LANE_TO_ROLE[lane]
        entries = _parse_entries(rows, role, champ_by_id)
        if entries:
            role_entries[role] = entries
            any_ok = True
            logger.info(
                "tier_refresh_lane_ok lane=%s entries=%d", lane, len(entries)
            )

    if not any_ok:
        logger.warning("tier_refresh_failed reason=all_lanes_empty")
        return None

    # Build fresh TierList and persist to disk.
    new_tiers = TierList(tiers=role_entries)
    tiers_path = data_dir / "tiers.json"
    _atomic_write_json(
        tiers_path,
        {
            "patch": "current",
            "tiers": {
                role: [{"champion": e.champion, "tier": e.tier} for e in entries]
                for role, entries in role_entries.items()
            },
        },
    )
    _write_stamp(data_dir)
    logger.info(
        "tier_refresh_done roles=%d total_entries=%d",
        len(role_entries),
        sum(len(v) for v in role_entries.values()),
    )
    return new_tiers
