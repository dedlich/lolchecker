"""Lolalytics counter fetcher — Tier 2.5 in the counter resolution pipeline.

One HTTP request per enemy lock-in. Fetches the enemy champion's build page
from Lolalytics and extracts matchup data embedded in the Qwik SSR state.
Results are cached on disk (1-week TTL keyed by patch+role+enemy). Falls
back silently so the LLM tier below never sees an exception.

Position in the resolution chain (app.py _lookup_counters):
  1. Static seed (counters.json) — instant
  2. Disk cache check — instant
  2.5. THIS — ~1-3 s, free, accurate
  3. LLM fallback (Groq/OpenRouter/Gemini) — ~3-8 s, uses API credits

Source: Lolalytics champion build page (SSR Qwik state embedded in HTML).
  GET https://lolalytics.com/lol/{champion_slug}/build/

The page embeds `enemy` matchup data per lane:
  enemy.{lane} = list of [{id, wr, n, ...}]
where `wr` = focal champion's win rate against this opponent.
Low wr → opponent is a strong counter.
We invert to get the opponent's win rate vs the focal champion.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import diskcache
import httpx

from .models import CounterEntry, Role

if TYPE_CHECKING:
    from .models import Champion

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20.0
CACHE_TTL = 7 * 24 * 3600  # 1 week; patch key causes natural rotation

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://lolalytics.com/",
}

_ROLE_TO_LANE: dict[str, str] = {
    "TOP":     "top",
    "JUNGLE":  "jungle",
    "MID":     "middle",
    "BOT":     "bottom",
    "SUPPORT": "support",
}

# Minimum games in the matchup to trust the win rate.
MIN_GAMES = 200

_LOLALYTICS_BUILD_URL = "https://lolalytics.com/lol/{slug}/build/"


# ---------------------------------------------------------------------------
# Qwik SSR state parser (shared logic with refresh.py)
# ---------------------------------------------------------------------------
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(ref: str) -> int | None:
    val = 0
    for c in ref:
        if c not in _BASE36:
            return None
        val = val * 36 + _BASE36.index(c)
    return val


def _parse_qwik_state(html: str) -> list[Any]:
    m = re.search(r'<script\s+type="qwik/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        raise ValueError("qwik/json script block not found")
    return json.loads(m.group(1))["objs"]


def _deref(objs: list[Any], ref: Any) -> Any:
    if not isinstance(ref, str):
        return ref
    raw = ref[1:] if ref.startswith("\x12") else ref
    idx = _b36(raw)
    if idx is None or idx >= len(objs):
        return ref
    return objs[idx]


# ---------------------------------------------------------------------------
# Parse counter rows from Qwik state
# ---------------------------------------------------------------------------

def _extract_counters(
    objs: list[Any],
    lane: str,
    champ_by_id: dict[int, "Champion"],
    *,
    limit: int = 5,
) -> list[CounterEntry]:
    """Navigate the Qwik object graph and return counter picks for *lane*."""
    # Find the useGetData gateway object
    gateway: dict[str, str] | None = None
    for obj in objs:
        if isinstance(obj, dict) and "K0pttOcjEAY" in obj and "tPXLxeCn1mM" in obj:
            gateway = obj
            break
    if gateway is None:
        raise ValueError("gateway object not found")

    data_idx = _b36(gateway["tPXLxeCn1mM"])
    if data_idx is None or data_idx >= len(objs):
        raise ValueError("tPXLxeCn1mM ref invalid")
    data_obj = objs[data_idx]
    if not isinstance(data_obj, dict) or "enemy" not in data_obj:
        raise ValueError("data object missing 'enemy' key")

    # enemy dict: {top: ref, jungle: ref, middle: ref, bottom: ref, support: ref}
    enemy_container_idx = _b36(data_obj["enemy"])
    if enemy_container_idx is None or enemy_container_idx >= len(objs):
        raise ValueError("enemy ref invalid")
    enemy_container = objs[enemy_container_idx]
    if not isinstance(enemy_container, dict) or lane not in enemy_container:
        raise ValueError(f"lane '{lane}' not in enemy container")

    # List of matchup rows for this lane
    lane_idx = _b36(enemy_container[lane])
    if lane_idx is None or lane_idx >= len(objs):
        raise ValueError(f"lane ref invalid for {lane}")
    rows = objs[lane_idx]
    if not isinstance(rows, list):
        raise ValueError(f"expected list for lane {lane}, got {type(rows)}")

    # Also parse enemy_h for column order (defensive — we know the schema but verify)
    enemy_h_ref = data_obj.get("enemy_h", "")
    h_idx = _b36(enemy_h_ref) if enemy_h_ref else None
    headers: list[str] = ["id", "wr", "d1", "d2", "pr", "n"]
    if h_idx is not None and h_idx < len(objs):
        raw_headers = objs[h_idx]
        if isinstance(raw_headers, list):
            decoded_headers = [_deref(objs, h) for h in raw_headers]
            if all(isinstance(h, str) for h in decoded_headers):
                headers = decoded_headers  # type: ignore[assignment]

    id_col = headers.index("id") if "id" in headers else 0
    wr_col = headers.index("wr") if "wr" in headers else 1
    n_col  = headers.index("n")  if "n"  in headers else 5

    parsed: list[tuple[float, str]] = []  # (counter_wr, champion_key)

    for row_ref in rows:
        row_idx = _b36(row_ref)
        if row_idx is None or row_idx >= len(objs):
            continue
        row = objs[row_idx]
        if not isinstance(row, list) or len(row) <= max(id_col, wr_col, n_col):
            continue

        champ_id = _deref(objs, row[id_col])
        focal_wr = _deref(objs, row[wr_col])
        games    = _deref(objs, row[n_col])

        if not isinstance(champ_id, int) or not isinstance(focal_wr, (int, float)):
            continue
        if not isinstance(games, (int, float)) or int(games) < MIN_GAMES:
            continue

        champ = champ_by_id.get(champ_id)
        if champ is None:
            continue

        # focal_wr = the enemy champion's win rate against this counter pick.
        # counter_wr = 100 - focal_wr = how well the counter pick beats the enemy.
        counter_wr = 100.0 - float(focal_wr)
        parsed.append((counter_wr, champ.key))

    # Best counters first (highest win rate vs enemy).
    parsed.sort(reverse=True)
    top = parsed[:limit]

    entries: list[CounterEntry] = []
    for wr, key in top:
        advantage = wr - 50.0
        score = min(10.0, max(0.0, 5.0 + advantage * 0.8))
        if wr >= 55.0:
            tier_grade = "S+"
        elif wr >= 53.0:
            tier_grade = "S"
        elif wr >= 52.0:
            tier_grade = "A"
        elif wr >= 51.0:
            tier_grade = "B"
        else:
            tier_grade = "C"
        entries.append(CounterEntry(champion=key, score=round(score, 1), tier=tier_grade))  # type: ignore[arg-type]

    return entries


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class LolalyticsCounterFetcher:
    """Async counter fetcher backed by Lolalytics SSR pages + a local disk cache.

    Thread-safety: all methods are coroutines designed to run on the shared
    qasync loop. The diskcache write is sync but GIL-protected.
    """

    def __init__(
        self,
        cache_dir: Path,
        champ_by_id: dict[int, "Champion"],
        *,
        tier: str = "emerald_plus",
        region: str = "all",
        patch: str = "current",
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._cache = diskcache.Cache(str(cache_dir))
        self._champ_by_id = champ_by_id
        self._tier = tier
        self._region = region
        self._patch = patch
        self._timeout = timeout

    def set_patch(self, patch: str) -> None:
        """Called by the hydration task when DataDragon reports the actual patch."""
        if patch and patch != self._patch:
            self._patch = patch

    def update_champion_map(self, champ_by_id: dict[int, "Champion"]) -> None:
        """Replace the champion map when DataDragon loads the full roster."""
        self._champ_by_id = champ_by_id

    # -- Cache helpers -------------------------------------------------------

    def _cache_key(self, enemy_key: str, role: Role) -> str:
        return f"lolalytics_counter:{self._patch}:{role}:{enemy_key}"

    def get_cached(self, enemy_key: str, role: Role) -> list[CounterEntry] | None:
        return self._cache.get(self._cache_key(enemy_key, role))

    def _store(self, enemy_key: str, role: Role, entries: list[CounterEntry]) -> None:
        if entries:
            self._cache.set(self._cache_key(enemy_key, role), entries, expire=CACHE_TTL)

    # -- Champion slug helper ------------------------------------------------

    @staticmethod
    def _to_slug(champion_key: str) -> str:
        """Convert a DataDragon champion key to a Lolalytics URL slug.

        DataDragon keys are PascalCase (e.g. 'AurelionSol', 'DrMundo').
        Lolalytics slugs are all-lowercase with no separators.
        """
        return champion_key.lower()

    # -- Network fetch -------------------------------------------------------

    async def fetch(self, enemy_key: str, role: Role) -> list[CounterEntry]:
        """Return counters for *enemy_key* in *role*. Never raises."""
        cached = self.get_cached(enemy_key, role)
        if cached is not None:
            logger.debug("lolalytics_counter_cache_hit enemy=%s role=%s", enemy_key, role)
            return cached

        lane = _ROLE_TO_LANE.get(role)
        if lane is None:
            return []

        slug = self._to_slug(enemy_key)
        url = _LOLALYTICS_BUILD_URL.format(slug=slug)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=_HEADERS)
                resp.raise_for_status()
                html = resp.text
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "lolalytics_counter_fetch_failed enemy=%s role=%s err=%s",
                enemy_key, role, exc,
            )
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (404, 429):
                self._cache.set(self._cache_key(enemy_key, role), [], expire=300)
            return []

        try:
            objs = _parse_qwik_state(html)
            entries = _extract_counters(objs, lane, self._champ_by_id)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.debug(
                "lolalytics_counter_parse_failed enemy=%s role=%s err=%s",
                enemy_key, role, exc,
            )
            return []

        if entries:
            self._store(enemy_key, role, entries)
            logger.info(
                "lolalytics_counter_fetched enemy=%s role=%s count=%d",
                enemy_key, role, len(entries),
            )
        else:
            logger.debug(
                "lolalytics_counter_empty enemy=%s role=%s",
                enemy_key, role,
            )
        return entries

    def close(self) -> None:
        try:
            self._cache.close()
        except Exception:  # noqa: BLE001
            pass
