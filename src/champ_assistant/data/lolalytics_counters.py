"""Lolalytics counter fetcher — Tier 2.5 in the counter resolution pipeline.

One HTTP request per enemy lock-in. Fetches the enemy champion's lane data
from Lolalytics and extracts which champions beat them most often. Results are
cached on disk (1-week TTL keyed by patch+role+enemy). Falls back silently so
the LLM tier below never sees an exception.

Position in the resolution chain (app.py _lookup_counters):
  1. Static seed (counters.json) — instant
  2. Disk cache check — instant
  2.5. THIS — ~1-2 s, free, accurate
  3. LLM fallback (Groq/OpenRouter/Gemini) — ~3-8 s, uses API credits

Endpoint:
  GET https://lolalytics.com/api/champion/{champion_id}/
      ?patch=current&tier={tier}&region={region}&lane={lane}&hv=3

Response shape (defensive — we accept several field-name variants):
  {
    "counters": [
      {"id": 516, "n": "Ornn", "wr": 45.8, "games": 12345}
      // wr = enemy champion's win rate vs this opponent;
      // LOW wr means the opponent counters the enemy.
    ]
  }
We invert: opponent win rate = 100 - enemy_wr. Opponents where the enemy
wins rarely are our strongest counter picks.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import diskcache
import httpx

from .models import CounterEntry, Role

if TYPE_CHECKING:
    from .models import Champion

logger = logging.getLogger(__name__)

LOLALYTICS_CHAMPION_URL = "https://lolalytics.com/api/champion/{champion_id}/"
REQUEST_TIMEOUT = 10.0
CACHE_TTL = 7 * 24 * 3600  # 1 week; patch key causes natural rotation

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://lolalytics.com/",
}

_ROLE_TO_LANE: dict[str, str] = {
    "TOP":     "top",
    "JUNGLE":  "jungle",
    "MID":     "mid",
    "BOT":     "adc",
    "SUPPORT": "support",
}

# Minimum games in the matchup to trust the win rate.
MIN_GAMES = 200


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_matchup_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Try several known field-name variants for matchup data.

    Lolalytics has rearranged their response schema across API versions.
    We walk a priority list of known keys and return the first list we find
    that looks like matchup rows (dicts with at least an 'id' and 'wr').
    """
    candidates: list[Any] = []

    # Direct keys tried in priority order.
    for key in ("counters", "matchups", "counter", "worst_matchups", "vs"):
        val = payload.get(key)
        if isinstance(val, list):
            candidates.append(val)
        elif isinstance(val, dict):
            # Some versions nest counters under e.g. {"counters": {"data": [...]}}
            inner = val.get("data") or val.get("counters") or val.get("list")
            if isinstance(inner, list):
                candidates.append(inner)

    # Walk nested under common wrapper keys.
    for wrapper in ("data", "champion", "stats"):
        sub = payload.get(wrapper)
        if not isinstance(sub, dict):
            continue
        for key in ("counters", "matchups", "counter", "worst_matchups"):
            val = sub.get(key)
            if isinstance(val, list):
                candidates.append(val)

    # Return the first candidate whose rows have the expected shape.
    for rows in candidates:
        if rows and isinstance(rows[0], dict) and "id" in rows[0]:
            return rows

    return []


def _parse_rows(
    rows: list[dict[str, Any]],
    champ_by_id: dict[int, "Champion"],
    *,
    limit: int = 5,
) -> list[CounterEntry]:
    """Convert raw matchup rows to CounterEntry objects.

    ``wr`` in the response is the *enemy's* win rate against this opponent.
    Low enemy win rate = the opponent is a strong counter.
    We invert to get the opponent's win rate vs the enemy, then rank by that.
    """
    parsed: list[tuple[float, str]] = []  # (counter_wr, champion_key)

    for row in rows:
        champ_id = row.get("id")
        enemy_wr = row.get("wr")
        games = row.get("games") or row.get("n_games") or 0
        if not isinstance(champ_id, int):
            continue
        if not isinstance(enemy_wr, float | int):
            continue
        if int(games) < MIN_GAMES:
            continue
        champ = champ_by_id.get(champ_id)
        if champ is None:
            continue
        counter_wr = 100.0 - float(enemy_wr)
        parsed.append((counter_wr, champ.key))

    # Best counters first (highest win rate vs enemy).
    parsed.sort(reverse=True)
    top = parsed[:limit]

    entries: list[CounterEntry] = []
    for i, (wr, key) in enumerate(top):
        # Derive a rough score (0-10) and tier from the win-rate advantage.
        advantage = wr - 50.0          # how far above 50% (negative = below)
        score = min(10.0, max(0.0, 5.0 + advantage * 0.8))
        if wr >= 55.0:
            tier = "S+"
        elif wr >= 53.0:
            tier = "S"
        elif wr >= 52.0:
            tier = "A"
        elif wr >= 51.0:
            tier = "B"
        else:
            tier = "C"
        entries.append(CounterEntry(champion=key, score=round(score, 1), tier=tier))  # type: ignore[arg-type]

    return entries


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class LolalyticsCounterFetcher:
    """Async counter fetcher backed by Lolalytics + a local disk cache.

    Thread-safety: all methods are coroutines designed to run on the shared
    qasync loop. The diskcache write is sync but GIL-protected.
    """

    def __init__(
        self,
        cache_dir: Path,
        champ_by_id: dict[int, "Champion"],
        *,
        tier: str = "platinum_plus",
        region: str = "euw",
        patch: str = "current",
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        self._cache = diskcache.Cache(str(cache_dir))
        self._champ_by_id = champ_by_id
        self._champ_id_by_key: dict[str, int] = {
            c.key: c.id for c in champ_by_id.values()
        }
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
        self._champ_id_by_key = {c.key: c.id for c in champ_by_id.values()}

    # -- Cache helpers -------------------------------------------------------

    def _cache_key(self, enemy_key: str, role: Role) -> str:
        return f"lolalytics_counter:{self._patch}:{role}:{enemy_key}"

    def get_cached(self, enemy_key: str, role: Role) -> list[CounterEntry] | None:
        return self._cache.get(self._cache_key(enemy_key, role))

    def _store(self, enemy_key: str, role: Role, entries: list[CounterEntry]) -> None:
        if entries:
            self._cache.set(self._cache_key(enemy_key, role), entries, expire=CACHE_TTL)

    # -- Network fetch -------------------------------------------------------

    async def fetch(self, enemy_key: str, role: Role) -> list[CounterEntry]:
        """Return counters for *enemy_key* in *role*. Never raises."""
        cached = self.get_cached(enemy_key, role)
        if cached is not None:
            logger.debug("lolalytics_counter_cache_hit enemy=%s role=%s", enemy_key, role)
            return cached

        champion_id = self._champ_id_by_key.get(enemy_key)
        if champion_id is None:
            logger.debug("lolalytics_counter_no_id enemy=%s", enemy_key)
            return []

        lane = _ROLE_TO_LANE.get(role)
        if lane is None:
            return []

        url = LOLALYTICS_CHAMPION_URL.format(champion_id=champion_id)
        params = {
            "patch": "current",
            "tier": self._tier,
            "region": self._region,
            "lane": lane,
            "hv": "3",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, params=params, headers=_HEADERS)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "lolalytics_counter_fetch_failed enemy=%s role=%s err=%s",
                enemy_key, role, exc,
            )
            return []

        rows = _extract_matchup_rows(payload)
        if not rows:
            logger.debug(
                "lolalytics_counter_no_matchup_data enemy=%s role=%s keys=%s",
                enemy_key, role, list(payload.keys())[:10],
            )
            return []

        entries = _parse_rows(rows, self._champ_by_id)
        if entries:
            self._store(enemy_key, role, entries)
            logger.info(
                "lolalytics_counter_fetched enemy=%s role=%s count=%d",
                enemy_key, role, len(entries),
            )
        return entries

    def close(self) -> None:
        try:
            self._cache.close()
        except Exception:  # noqa: BLE001
            pass
