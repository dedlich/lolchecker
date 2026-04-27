"""Live counter fetching via Groq's free LLM API + persistent disk cache.

Why Groq: free tier covers ~14000 requests/day with no cost — combined with
1-week cache, after a handful of champ-select sessions the cache contains
every matchup the user encounters and the API stops being called.

Setup: user signs up at https://console.groq.com (free), copies their API
key into ``.env`` next to the exe as ``GROQ_API_KEY=<key>``. Without a key
the store is disabled (``enabled == False``) and ``get()`` always returns
the cached value or an empty list — graceful degradation, never blocks
the app from running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import diskcache
import httpx

from .models import CounterEntry, Role

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TIMEOUT = 8.0
DEFAULT_CACHE_TTL = 7 * 24 * 60 * 60  # 1 week — matchups are patch-stable


_SYSTEM_PROMPT = """You are a League of Legends matchup expert. Reply with JSON only.

Given an enemy champion + role, return a JSON object with field "counters"
containing 5 counter picks. Each entry has:
  champion: <Riot's exact champion key — no spaces, e.g. "Darius",
            "MissFortune", "KSante", "LeeSin", "ChoGath", "DrMundo">
  score:    <float 0-10, how strongly this counters the enemy>
  tier:     <one of "S+", "S", "A", "B", "C", "D">

Rules:
- Champion KEYS only (Riot's id format, no spaces or apostrophes).
- Counters must fit the role (e.g. counter for Yasuo MID differs from TOP).
- Be opinionated — strongest counters first.
- Output ONLY the JSON object, no preamble, no markdown.
"""


class RuntimeCounterStore:
    """Async-safe lookup with dedup, persistent cache, and graceful no-key fallback."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        client: httpx.AsyncClient | None = None,
        patch: str = "current",
    ) -> None:
        self.cache = diskcache.Cache(str(cache_dir))
        self.api_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.patch = patch
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._inflight: dict[tuple[str, str], asyncio.Task[list[CounterEntry]]] = {}

    # -- Sync surface ----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def get_cached(self, enemy_key: str, role: Role) -> list[CounterEntry] | None:
        """Returns cached counters without ever hitting the network."""
        return self.cache.get(self._cache_key(enemy_key, role))

    # -- Async surface ---------------------------------------------------

    async def get(self, enemy_key: str, role: Role) -> list[CounterEntry]:
        cached = self.get_cached(enemy_key, role)
        if cached is not None:
            return cached
        if not self.enabled:
            return []
        return await self._fetch_dedup(enemy_key, role)

    async def _fetch_dedup(
        self, enemy_key: str, role: Role
    ) -> list[CounterEntry]:
        key = (enemy_key, role)
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        task = asyncio.create_task(self._fetch(enemy_key, role))
        self._inflight[key] = task
        try:
            return await task
        finally:
            self._inflight.pop(key, None)

    async def _fetch(self, enemy_key: str, role: Role) -> list[CounterEntry]:
        prompt = (
            f"Counters against {enemy_key} in {role}? "
            "Return a JSON object with field 'counters' containing 5 counter picks."
        )
        try:
            response = await self._client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 1024,
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "groq_http_error",
                extra={"enemy": enemy_key, "role": role,
                       "error_type": type(exc).__name__},
            )
            return []
        except Exception:  # noqa: BLE001
            logger.exception("groq_unexpected_error")
            return []

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("groq_response_shape_unexpected", extra={"data": str(data)[:300]})
            return []

        try:
            counters = self._parse_counters(content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "groq_parse_failed",
                extra={"enemy": enemy_key, "role": role, "error": str(exc),
                       "snippet": content[:200]},
            )
            return []
        if not counters:
            return []

        self.cache.set(
            self._cache_key(enemy_key, role), counters, expire=self.cache_ttl
        )
        logger.info(
            "groq_counters_fetched",
            extra={"enemy": enemy_key, "role": role, "count": len(counters)},
        )
        return counters

    # -- Helpers ---------------------------------------------------------

    def _cache_key(self, enemy_key: str, role: Role) -> str:
        return f"runtime_counter:{self.patch}:{role}:{enemy_key}"

    @staticmethod
    def _parse_counters(text: str) -> list[CounterEntry]:
        # Defensive: strip ```json ... ``` fences if Llama added them despite
        # response_format=json_object.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        data = json.loads(text)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("counters") or []
        else:
            raise ValueError("expected JSON object or array")
        if not isinstance(items, list):
            raise ValueError("'counters' must be an array")
        return [CounterEntry.model_validate(item) for item in items]

    async def aclose(self) -> None:
        if self._owns_client:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("runtime_counter_close_error")
        self.cache.close()
