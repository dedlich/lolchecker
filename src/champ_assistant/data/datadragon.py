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
CHAMPION_ICON_URL_TEMPLATE = f"{DDRAGON_BASE}/cdn/{{patch}}/img/champion/{{key}}.png"
SPELL_ICON_URL_TEMPLATE = f"{DDRAGON_BASE}/cdn/{{patch}}/img/spell/{{file}}"
ITEM_ICON_URL_TEMPLATE = f"{DDRAGON_BASE}/cdn/{{patch}}/img/item/{{item_id}}.png"
RUNES_REFORGED_URL_TEMPLATE = (
    f"{DDRAGON_BASE}/cdn/{{patch}}/data/en_US/runesReforged.json"
)
RUNE_ICON_URL_TEMPLATE = f"{DDRAGON_BASE}/cdn/img/{{icon_path}}"

# LCDA returns spell display names; Data Dragon stores them under file IDs
# starting with ``Summoner``. This map covers every spell currently usable
# in any LoL queue (Classic + ARAM).
SUMMONER_SPELL_FILES: dict[str, str] = {
    "Flash": "SummonerFlash.png",
    "Ignite": "SummonerDot.png",
    "Heal": "SummonerHeal.png",
    "Teleport": "SummonerTeleport.png",
    "Cleanse": "SummonerBoost.png",
    "Barrier": "SummonerBarrier.png",
    "Exhaust": "SummonerExhaust.png",
    "Smite": "SummonerSmite.png",
    "Ghost": "SummonerHaste.png",
    "Snowball": "SummonerSnowball.png",
}


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

    # -- Icons -----------------------------------------------------------

    async def fetch_champion_icon(self, patch: str, champion_key: str) -> bytes:
        """Return PNG bytes for the champion portrait, cached on disk."""
        cache_key = f"ddragon:icon:{patch}:{champion_key}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        url = CHAMPION_ICON_URL_TEMPLATE.format(patch=patch, key=champion_key)
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            raise DataDragonError(
                f"icon fetch failed for {champion_key} on {patch}: {exc}"
            ) from exc

        self.cache.set(cache_key, data, expire=self.TTL_CHAMPIONS)
        return data

    async def prefetch_icons(
        self,
        patch: str,
        champion_keys: list[str],
        *,
        concurrency: int = 8,
    ) -> dict[str, bytes]:
        """Fetch every icon in parallel; return key → PNG bytes.

        Failed icons are skipped (logged, not raised) so a single 404
        doesn't take down the whole prefetch.
        """
        import asyncio

        sem = asyncio.Semaphore(concurrency)

        async def one(key: str) -> tuple[str, bytes | None]:
            async with sem:
                try:
                    return key, await self.fetch_champion_icon(patch, key)
                except DataDragonError as exc:
                    logger.warning(
                        "ddragon_icon_skipped",
                        extra={"champion": key, "error": str(exc)},
                    )
                    return key, None

        results = await asyncio.gather(*(one(k) for k in champion_keys))
        return {k: data for k, data in results if data is not None}

    async def fetch_spell_icon(self, patch: str, spell_name: str) -> bytes:
        """Fetch a summoner spell icon (PNG bytes), cached on disk."""
        file = SUMMONER_SPELL_FILES.get(spell_name)
        if file is None:
            raise DataDragonError(f"unknown summoner spell: {spell_name}")
        cache_key = f"spell:{patch}:{spell_name}"
        cached = self.cache.get(cache_key)
        if isinstance(cached, bytes):
            return cached
        if self._client is None:
            raise DataDragonError("not in async context")
        try:
            response = await self._client.get(
                SPELL_ICON_URL_TEMPLATE.format(patch=patch, file=file)
            )
            response.raise_for_status()
            data = response.content
        except (httpx.HTTPError, ValueError) as exc:
            raise DataDragonError(
                f"spell icon fetch failed for {spell_name} on {patch}: {exc}"
            ) from exc
        self.cache.set(cache_key, data, expire=self.TTL_CHAMPIONS)
        return data

    async def prefetch_spell_icons(self, patch: str) -> dict[str, bytes]:
        """Fetch every known summoner spell icon in parallel."""
        import asyncio

        async def one(name: str) -> tuple[str, bytes | None]:
            try:
                return name, await self.fetch_spell_icon(patch, name)
            except DataDragonError as exc:
                logger.warning("ddragon_spell_icon_skipped %s: %s", name, exc)
                return name, None

        results = await asyncio.gather(
            *(one(n) for n in SUMMONER_SPELL_FILES)
        )
        return {n: data for n, data in results if data is not None}

    async def fetch_item_icon(self, patch: str, item_id: int) -> bytes:
        """Return PNG bytes for an item icon, cached on disk."""
        cache_key = f"ddragon:item:{patch}:{item_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        url = ITEM_ICON_URL_TEMPLATE.format(patch=patch, item_id=item_id)
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            raise DataDragonError(
                f"item icon fetch failed for {item_id} on {patch}: {exc}"
            ) from exc

        self.cache.set(cache_key, data, expire=self.TTL_CHAMPIONS)
        return data

    async def fetch_rune_paths(self, patch: str) -> dict[int, str]:
        """Fetch ``runesReforged.json`` and return ``{perk_id: icon_path}``.
        The icon path is relative to ``cdn/img/`` (e.g.
        ``perk-images/Styles/Precision/Conqueror/Conqueror.png``).
        Includes both keystones / minor runes AND tree-style icons —
        callers filter by which IDs they care about."""
        cache_key = f"ddragon:runes_reforged:{patch}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            assert isinstance(cached, dict)
            return cached

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        url = RUNES_REFORGED_URL_TEMPLATE.format(patch=patch)
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DataDragonError(
                f"runes_reforged fetch failed for {patch}: {exc}"
            ) from exc

        out: dict[int, str] = {}
        for tree in data:
            if not isinstance(tree, dict):
                continue
            tree_id = tree.get("id")
            tree_icon = tree.get("icon")
            if isinstance(tree_id, int) and isinstance(tree_icon, str):
                out[tree_id] = tree_icon
            for slot in tree.get("slots") or []:
                for perk in slot.get("runes") or []:
                    pid = perk.get("id")
                    icon = perk.get("icon")
                    if isinstance(pid, int) and isinstance(icon, str):
                        out[pid] = icon

        self.cache.set(cache_key, out, expire=self.TTL_CHAMPIONS)
        return out

    async def fetch_rune_icon(self, icon_path: str) -> bytes:
        """Return PNG bytes for a rune icon at ``icon_path`` (relative
        to cdn/img/), cached on disk by the path itself — no patch
        dependency since rune icons are stable across patches."""
        cache_key = f"ddragon:rune_icon:{icon_path}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        if self._client is None:
            raise RuntimeError("DataDragon must be used as an async context manager")

        url = RUNE_ICON_URL_TEMPLATE.format(icon_path=icon_path)
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            raise DataDragonError(
                f"rune icon fetch failed for {icon_path}: {exc}"
            ) from exc

        self.cache.set(cache_key, data, expire=self.TTL_CHAMPIONS)
        return data

    async def prefetch_rune_icons(
        self,
        patch: str,
        perk_ids: list[int],
        *,
        concurrency: int = 8,
    ) -> dict[int, bytes]:
        """Fetch rune icons for the given perk IDs in parallel; return
        id → PNG bytes. Pairs with PERK_IDS in perks_data.py — callers
        pass ``list(PERK_IDS.values())`` to prefetch every rune they
        might surface in a build display."""
        import asyncio
        try:
            paths = await self.fetch_rune_paths(patch)
        except DataDragonError as exc:
            logger.warning("ddragon_runes_reforged_failed: %s", exc)
            return {}

        sem = asyncio.Semaphore(concurrency)

        async def one(perk_id: int) -> tuple[int, bytes | None]:
            path = paths.get(perk_id)
            if path is None:
                return perk_id, None
            async with sem:
                try:
                    return perk_id, await self.fetch_rune_icon(path)
                except DataDragonError as exc:
                    logger.warning("ddragon_rune_icon_skipped %d: %s", perk_id, exc)
                    return perk_id, None

        results = await asyncio.gather(*(one(p) for p in perk_ids))
        return {pid: data for pid, data in results if data is not None}

    async def prefetch_item_icons(
        self,
        patch: str,
        item_ids: list[int],
        *,
        concurrency: int = 8,
    ) -> dict[int, bytes]:
        """Fetch every item icon in parallel; return id → PNG bytes.
        Failed fetches are skipped (logged, not raised) so a single
        404 doesn't take down the whole prefetch — same contract as
        prefetch_icons for champions."""
        import asyncio

        sem = asyncio.Semaphore(concurrency)

        async def one(item_id: int) -> tuple[int, bytes | None]:
            async with sem:
                try:
                    return item_id, await self.fetch_item_icon(patch, item_id)
                except DataDragonError as exc:
                    logger.warning("ddragon_item_icon_skipped %d: %s", item_id, exc)
                    return item_id, None

        results = await asyncio.gather(*(one(i) for i in item_ids))
        return {i: data for i, data in results if data is not None}
