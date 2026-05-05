"""Static data refresh service — pulls live tier lists from Lolalytics.

Runs once at startup (as a background task) and every TTL_S hours thereafter.
On success: writes updated ``tiers.json`` atomically and hands a new
``TierList`` to the caller. On any network / parse failure: existing files
are untouched and ``None`` is returned so callers can keep their current data.

Source: Lolalytics tier list page (SSR Qwik state embedded in HTML).
The page embeds full champion stats per role; we parse the Qwik JSON state
block to extract win rates and build our tier list without any undocumented
API calls.

Endpoint:
  GET https://lolalytics.com/lol/tierlist/?tier={tier}&region={region}

Counter data is handled lazily per-champion in ``lolalytics_counters.py``.
Builds are not refreshed here — the LCU push layer reads from builds.json
which is updated out-of-band (manual curation or a future separate fetch).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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
LOLALYTICS_TIERLIST_URL = "https://lolalytics.com/lol/tierlist/"
REFRESH_TTL_S: float = 6 * 3600        # re-fetch after 6 hours
REQUEST_TIMEOUT: float = 20.0           # SSR pages are larger

_STAMP_FILENAME = ".tiers_refreshed_at"

_LANE_TO_ROLE: dict[str, Role] = {
    "top":     "TOP",
    "jungle":  "JUNGLE",
    "middle":  "MID",
    "bottom":  "BOT",
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
# Qwik SSR state parser
# ---------------------------------------------------------------------------
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(ref: str) -> int | None:
    """Decode a base-36 reference string to an integer index."""
    val = 0
    for c in ref:
        if c not in _BASE36:
            return None
        val = val * 36 + _BASE36.index(c)
    return val


def _parse_qwik_state(html: str) -> list[Any]:
    """Extract the Qwik SSR object array from an HTML page."""
    m = re.search(r'<script\s+type="qwik/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("qwik/json script block not found")
    return json.loads(m.group(1))["objs"]


def _deref(objs: list[Any], ref: Any) -> Any:
    """Resolve a Qwik reference string to its value in *objs*."""
    if not isinstance(ref, str):
        return ref
    # Strip Qwik signal prefix (\x12 = reactive signal wrapper)
    raw = ref[1:] if ref.startswith("\x12") else ref
    idx = _b36(raw)
    if idx is None or idx >= len(objs):
        return ref
    return objs[idx]


def _parse_tierlist_from_state(
    objs: list[Any],
    champ_by_id: dict[int, "Champion"],
) -> dict[Role, list[TierEntry]]:
    """Walk the Qwik object graph and return a role → TierEntry list mapping."""
    # Find the dict that holds useGetData/useGetConstants Qwik action keys.
    # This dict always has both 'K0pttOcjEAY' and 'tPXLxeCn1mM'.
    gateway: dict[str, str] | None = None
    for obj in objs:
        if isinstance(obj, dict) and "K0pttOcjEAY" in obj and "tPXLxeCn1mM" in obj:
            gateway = obj
            break
    if gateway is None:
        raise ValueError("gateway object not found in Qwik state")

    # useGetData → tierlist root object
    tl_idx = _b36(gateway["tPXLxeCn1mM"])
    if tl_idx is None or tl_idx >= len(objs):
        raise ValueError("tPXLxeCn1mM ref invalid")
    tl_obj = objs[tl_idx]
    if not isinstance(tl_obj, dict) or "cid" not in tl_obj:
        raise ValueError("tierlist object missing 'cid' key")

    # cid → {champion_id_str: stats_ref}
    cid_idx = _b36(tl_obj["cid"])
    if cid_idx is None or cid_idx >= len(objs):
        raise ValueError("cid ref invalid")
    cid_map = objs[cid_idx]
    if not isinstance(cid_map, dict):
        raise ValueError(f"cid is not a dict: {type(cid_map)}")

    role_entries: dict[Role, list[tuple[float, TierEntry]]] = {}

    for champ_id_str, stats_ref in cid_map.items():
        try:
            champ_id = int(champ_id_str)
        except ValueError:
            continue

        champ = champ_by_id.get(champ_id)
        if champ is None:
            continue

        stats_idx = _b36(stats_ref)
        if stats_idx is None or stats_idx >= len(objs):
            continue
        stats = objs[stats_idx]
        if not isinstance(stats, dict):
            continue

        # Resolve win rate and lane
        wr = _deref(objs, stats.get("wr"))
        lane = _deref(objs, stats.get("lane"))
        if not isinstance(wr, (int, float)) or not isinstance(lane, str):
            continue

        role = _LANE_TO_ROLE.get(lane)
        if role is None:
            continue

        grade = _wr_to_tier(float(wr))
        entry = TierEntry(champion=champ.key, tier=grade)  # type: ignore[arg-type]
        role_entries.setdefault(role, []).append((float(wr), entry))

    # Sort each role: S+ first, within same grade by win rate descending.
    _order = {"S+": 0, "S": 1, "A": 2, "B": 3, "C": 4, "D": 5}
    result: dict[Role, list[TierEntry]] = {}
    for role, wr_entries in role_entries.items():
        wr_entries.sort(key=lambda x: (_order.get(x[1].tier, 9), -x[0]))
        result[role] = [e for _, e in wr_entries]

    return result


# ---------------------------------------------------------------------------
# Network fetch
# ---------------------------------------------------------------------------

async def _fetch_tierlist_page(
    client: httpx.AsyncClient,
    *,
    tier: str,
    region: str,
) -> str:
    """Fetch the Lolalytics tier list HTML page. Returns HTML on success."""
    params = {"tier": tier, "region": region}
    try:
        resp = await client.get(
            LOLALYTICS_TIERLIST_URL,
            params=params,
            headers=_HEADERS,
        )
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError as exc:
        logger.warning("lolalytics_tierlist_fetch_failed tier=%s region=%s err=%s",
                       tier, region, exc)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def maybe_refresh(
    data_dir: Path,
    champ_by_id: dict[int, "Champion"],
    *,
    tier: str = "emerald_plus",
    region: str = "all",
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
        Lolalytics tier filter. ``"emerald_plus"`` is the recommended
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

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        html = await _fetch_tierlist_page(client, tier=tier, region=region)

    if not html:
        logger.warning("tier_refresh_failed reason=empty_response")
        return None

    try:
        objs = _parse_qwik_state(html)
        role_entries = _parse_tierlist_from_state(objs, champ_by_id)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("tier_refresh_parse_failed err=%s", exc)
        return None

    if not role_entries:
        logger.warning("tier_refresh_failed reason=no_entries_parsed")
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
