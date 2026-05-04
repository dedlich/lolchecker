"""Meraki Analytics API client.

Provides structured champion and item data with detailed stats:
  https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/champions/{key}.json
  https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US/items.json

Cached on disk for 1 week — items and champion archetypes don't change
between daily logins, only on patch boundaries.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import diskcache
import httpx

logger = logging.getLogger(__name__)

_BASE = "https://cdn.merakianalytics.com/riot/lol/resources/latest/en-US"
_CHAMPION_URL = _BASE + "/champions/{key}.json"
_ITEMS_URL = _BASE + "/items.json"


class MerakiError(Exception):
    """Network or parse failure talking to Meraki Analytics CDN."""


class MerakiClient:
    """Async Meraki data fetcher with disk-cached responses.

    Use as an async context manager for the duration of the LCDA session —
    the underlying httpx client stays open for connection reuse, and the
    diskcache persists across restarts.
    """

    TTL = 7 * 24 * 60 * 60   # 1 week — data only changes on patches

    def __init__(
        self,
        cache_dir: Path,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cache = diskcache.Cache(str(cache_dir))
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MerakiClient":
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._cache.close()

    # ── Champion ──────────────────────────────────────────────────────────

    async def fetch_champion(self, meraki_key: str) -> dict:
        """Fetch one champion's full Meraki data dict.

        ``meraki_key`` is DataDragon's string key (e.g. "MissFortune")
        which matches Meraki's URL segment exactly.
        """
        cache_key = f"meraki:champ:{meraki_key}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        if self._client is None:
            raise RuntimeError("MerakiClient must be used as async context manager")

        url = _CHAMPION_URL.format(key=meraki_key)
        try:
            r = await self._client.get(url)
            r.raise_for_status()
            data: dict = r.json()
        except httpx.HTTPError as exc:
            raise MerakiError(f"champion fetch failed ({meraki_key}): {exc}") from exc
        except ValueError as exc:
            raise MerakiError(f"champion JSON parse error ({meraki_key}): {exc}") from exc

        if not isinstance(data, dict):
            raise MerakiError(f"unexpected champion payload type for {meraki_key}")

        self._cache.set(cache_key, data, expire=self.TTL)
        logger.info("meraki_champion_fetched key=%s", meraki_key)
        return data

    # ── Items ─────────────────────────────────────────────────────────────

    async def fetch_items(self) -> dict[str, dict]:
        """Fetch the full items catalogue (all patches, all modes).

        Returns a dict keyed by item id (string). The caller filters to
        SR-only completed items (id < 10000, tier >= 2, purchasable).
        """
        cache_key = "meraki:items"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        if self._client is None:
            raise RuntimeError("MerakiClient must be used as async context manager")

        try:
            r = await self._client.get(_ITEMS_URL)
            r.raise_for_status()
            data: dict = r.json()
        except httpx.HTTPError as exc:
            raise MerakiError(f"items fetch failed: {exc}") from exc
        except ValueError as exc:
            raise MerakiError(f"items JSON parse error: {exc}") from exc

        if not isinstance(data, dict):
            raise MerakiError(f"unexpected items payload type: {type(data)}")

        self._cache.set(cache_key, data, expire=self.TTL)
        logger.info("meraki_items_fetched count=%d", len(data))
        return data
