"""Riot Data Dragon client.

Data Dragon is the static-data CDN for League of Legends:
  - https://ddragon.leagueoflegends.com/api/versions.json     (patch list)
  - https://ddragon.leagueoflegends.com/cdn/{p}/data/en_US/champion.json

We cache responses on disk (``diskcache``) so:
  - we don't hammer Riot on every startup
  - we can run offline once data is fetched

Cache TTLs (masterplan §8 — Static Data einmalig laden):
  - versions: 1 hour (the list of patches changes when a new patch ships)
  - champions for a frozen patch: 1 week (immutable in practice; the CDN
    rewrites only on hotfixes)

Note on Riot's confusing ``key``/``id`` naming in the JSON:
  - JSON ``key`` is the *numeric* champion id (string-encoded), e.g. "266"
  - JSON ``id`` is the *string* key, e.g. "Aatrox"
We map both onto our :class:`Champion` model where ``id`` is int and
``key`` is the string identifier.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import diskcache
import httpx

from .models import Champion

logger = logging.getLogger(__name__)

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"
VERSIONS_URL = f"{DDRAGON_BASE}/api/versions.json"
CHAMPIONS_URL_TEMPLATE = f"{DDRAGON_BASE}/cdn/{{patch}}/data/en_US/champion.json"


class DataDragonError(Exception):
    """Network or parse failure talking to Data Dragon."""


class DataDragon:
    """Fetches + caches static champion / patch data from Riot's CDN."""

    DEFAULT_TIMEOUT = 5.0
    TTL_VERSIONS = 60 * 60                 # 1 hour
    TTL_CHAMPIONS = 7 * 24 * 60 * 60       # 1 week

    def __init__(
        self,
        cache_dir: Path,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache = diskcache.Cache(str(cache_dir))
        self.timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> DataDragon:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.AsyncClient(**kwargs)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.cache.close()

    # -- Patch / version --------------------------------------------------

    async def fetch_latest_patch(self) -> str:
        """Return the latest patch string, e.g. ``"14.8.1"``."""
        cached = self.cache.get("ddragon:versions")
        if cached is not None:
            return cached[0]

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        try:
            response = await self._client.get(VERSIONS_URL)
            response.raise_for_status()
            versions = response.json()
        except httpx.HTTPError as exc:
            raise DataDragonError(f"versions fetch failed: {exc}") from exc
        except ValueError as exc:
            raise DataDragonError(f"versions response not JSON: {exc}") from exc

        if not isinstance(versions, list) or not versions:
            raise DataDragonError(f"versions response is not a non-empty list: {versions!r}")

        self.cache.set("ddragon:versions", versions, expire=self.TTL_VERSIONS)
        return versions[0]

    # -- Champions --------------------------------------------------------

    async def fetch_champions(self, patch: str) -> dict[int, Champion]:
        """Return champions for ``patch`` keyed by numeric champion id."""
        cache_key = f"ddragon:champions:{patch}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        url = CHAMPIONS_URL_TEMPLATE.format(patch=patch)
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise DataDragonError(f"champions fetch failed for {patch}: {exc}") from exc
        except ValueError as exc:
            raise DataDragonError(f"champions response not JSON: {exc}") from exc

        if not isinstance(payload, dict) or "data" not in payload:
            raise DataDragonError(f"champions response missing 'data' key: {payload!r}")

        result: dict[int, Champion] = {}
        for raw in payload["data"].values():
            try:
                champ = Champion(
                    id=int(raw["key"]),       # numeric id (Riot calls this "key")
                    key=str(raw["id"]),       # string key (Riot calls this "id")
                    name=str(raw["name"]),
                    tags=list(raw.get("tags") or []),
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning(
                    "ddragon_champion_skipped",
                    extra={"raw_id": raw.get("id"), "error": str(exc)},
                )
                continue
            result[champ.id] = champ

        if not result:
            raise DataDragonError(f"no parseable champions in payload for {patch}")

        self.cache.set(cache_key, result, expire=self.TTL_CHAMPIONS)
        return result
