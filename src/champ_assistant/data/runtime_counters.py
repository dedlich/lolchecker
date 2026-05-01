"""Live counter fetching via a pluggable LLM provider + persistent disk cache.

Supported providers (selectable from Settings):
  - OpenRouter (default — easy signup, supports free Llama-3.3-70B)
  - Groq (faster but signup has had loop issues for some users)
  - Gemini (Google AI Studio, free quota)

All three accept the same OpenAI-compatible chat-completions payload —
only the URL, default model, and a tiny header difference vary.

Without a key the store is disabled (``enabled == False``) and ``get()``
always returns the cached value or an empty list — graceful degradation,
never blocks the app from running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import diskcache
import httpx

from .models import CounterEntry, Role

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmProvider:
    name: str           # "openrouter" / "groq" / "gemini"
    url: str            # chat completions endpoint
    default_model: str  # safe free-tier default
    signup_url: str
    extra_headers: dict[str, str] | None = None


PROVIDERS: dict[str, LlmProvider] = {
    "openrouter": LlmProvider(
        name="openrouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        default_model="meta-llama/llama-3.3-70b-instruct:free",
        signup_url="https://openrouter.ai",
        extra_headers={
            "HTTP-Referer": "https://github.com/dedlich/lolchecker",
            "X-Title": "Champ Assistant",
        },
    ),
    "groq": LlmProvider(
        name="groq",
        url="https://api.groq.com/openai/v1/chat/completions",
        default_model="llama-3.3-70b-versatile",
        signup_url="https://console.groq.com",
    ),
    "gemini": LlmProvider(
        name="gemini",
        # Gemini uses an OpenAI-compatible shim path
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        default_model="gemini-2.0-flash",
        signup_url="https://aistudio.google.com/apikey",
    ),
}

DEFAULT_PROVIDER = "openrouter"


# Backwards-compat: tests + older fixtures still reference these constants.
GROQ_API_URL = PROVIDERS["groq"].url
DEFAULT_MODEL = PROVIDERS[DEFAULT_PROVIDER].default_model
DEFAULT_TIMEOUT = 8.0
DEFAULT_CACHE_TTL = 365 * 24 * 60 * 60  # ~1 year. The cache key includes the
                                       # current LoL patch — entries become
                                       # unreachable on patch change without
                                       # needing a TTL, so this is just a
                                       # safety net to keep diskcache from
                                       # accumulating forever.


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
    """Async-safe lookup with dedup, persistent cache, and graceful no-key fallback.

    Resolution order inside ``get()``:
      1. Disk cache (any previously stored result)
      2. Lolalytics pre-fetcher if one was set via ``set_lolalytics()``
      3. LLM fetch (requires API key)
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        api_key: str | None = None,
        provider: str | LlmProvider = DEFAULT_PROVIDER,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        client: httpx.AsyncClient | None = None,
        patch: str = "current",
    ) -> None:
        self._cache_dir = str(cache_dir)
        self._cache: "diskcache.Cache | None" = None  # lazy — opened on first access
        # Resolve provider config — accepts a name string or an explicit
        # LlmProvider instance (used by tests).
        if isinstance(provider, str):
            self.provider = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
        else:
            self.provider = provider
        # API-key resolution order: explicit arg → env var matching the
        # provider name → empty (disabled).
        if api_key is not None:
            self.api_key = api_key
        else:
            env_var = f"{self.provider.name.upper()}_API_KEY"
            self.api_key = os.environ.get(env_var, "") or os.environ.get("GROQ_API_KEY", "")
        self.model = model or self.provider.default_model
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.patch = patch
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._inflight: dict[tuple[str, str], asyncio.Task[list[CounterEntry]]] = {}
        self._lolalytics: object | None = None  # LolalyticsCounterFetcher, set post-init

    # -- Sync surface ----------------------------------------------------

    @property
    def cache(self) -> "diskcache.Cache":
        if self._cache is None:
            self._cache = diskcache.Cache(self._cache_dir)
        return self._cache

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def set_lolalytics(self, fetcher: object) -> None:
        """Attach a LolalyticsCounterFetcher as Tier 2.5 in the resolution chain.

        Called from _hydrate_champions_and_icons once the champion map is ready.
        Accepts ``object`` to avoid a circular import; duck-typed at call site.
        """
        self._lolalytics = fetcher

    def set_patch(self, patch: str) -> None:
        """Switch the cache namespace when Data Dragon reports a new patch.

        Old entries keyed under the previous patch remain on disk but stop
        being read (cache key includes the patch). They expire via the TTL
        safety net eventually.
        """
        if patch and patch != self.patch:
            logger.info("runtime_counter_patch_changed from=%s to=%s", self.patch, patch)
            self.patch = patch

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
        # Tier 2.5 — Lolalytics (fast, free, no API key required).
        if self._lolalytics is not None:
            try:
                lolalytics_result: list[CounterEntry] = await self._lolalytics.fetch(  # type: ignore[attr-defined]
                    enemy_key, role
                )
                if lolalytics_result:
                    self.cache.set(
                        self._cache_key(enemy_key, role),
                        lolalytics_result,
                        expire=self.cache_ttl,
                    )
                    return lolalytics_result
            except Exception:  # noqa: BLE001
                logger.debug("lolalytics_prefetch_error enemy=%s role=%s", enemy_key, role)

        # Tier 3 — LLM fallback.
        if not self.enabled:
            return []

        prompt = (
            f"Counters against {enemy_key} in {role}? "
            "Return a JSON object with field 'counters' containing 5 counter picks."
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider.extra_headers:
            headers.update(self.provider.extra_headers)
        try:
            response = await self._client.post(
                self.provider.url,
                headers=headers,
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
                "llm_http_error provider=%s err=%s", self.provider.name, exc,
                extra={"enemy": enemy_key, "role": role,
                       "error_type": type(exc).__name__},
            )
            return []
        except Exception:  # noqa: BLE001
            logger.exception("llm_unexpected_error provider=%s", self.provider.name)
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
        if self._cache is not None:
            self._cache.close()
