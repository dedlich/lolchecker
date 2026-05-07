"""Champion-specific game-plan prose via the LLM provider already wired
for ``data/runtime_counters``.

The service produces a short coaching paragraph for a locked-in champion
in their assigned role against the confirmed enemy team. The result is
cached on disk by ``(champ, role, sorted-ally-hash, sorted-enemy-hash,
patch)`` so back-to-back champ-select polls don't re-pay the API cost.

Without an API key configured the service is disabled — ``get_cached``
always returns the cached value or ``None``, ``prefetch`` no-ops.
Graceful degradation: the LiveCompanion right column shows the
placeholder text when no cached prose is available.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time as _time
from pathlib import Path

import diskcache
import httpx

from ..data.runtime_counters import DEFAULT_PROVIDER, PROVIDERS, LlmProvider

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
DEFAULT_CACHE_TTL = 365 * 24 * 60 * 60  # ~1 year; patch key invalidates faster
MAX_TOKENS = 350

_SYSTEM_PROMPT = """You are a League of Legends coach.

Given the active player's locked-in champion + role, their ally team,
and the confirmed enemy team, write ONE concise coaching paragraph
(3-5 sentences) covering:
  1. The champion's win condition this game.
  2. The matchup that matters most (which enemy threatens you most or
     which one you can exploit).
  3. The tempo plan — when to play safe, when to push, when to roam.

Rules:
  * Plain prose, no bullets, no markdown, no headings.
  * Speak directly to the player ("you", "your").
  * No filler. No "in conclusion". No "remember to".
  * Keep under 350 tokens.
"""


class GamePlanLLMService:
    """Async cache + LLM fetch for champion game-plan prose."""

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
        self._cache: "diskcache.Cache | None" = None  # lazy
        if isinstance(provider, str):
            self.provider = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
        else:
            self.provider = provider
        if api_key is not None:
            self.api_key = api_key
        else:
            env_var = f"{self.provider.name.upper()}_API_KEY"
            self.api_key = os.environ.get(env_var, "") or os.environ.get(
                "GROQ_API_KEY", ""
            )
        self.model = model or self.provider.default_model
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.patch = patch
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._inflight: dict[str, asyncio.Task[str]] = {}
        # Global cooldown after a rate-limit / payment / auth failure.
        # The per-key disk cache won't save us here because the cache key
        # includes ally + enemy hashes that change on every champ-select
        # tick — we'd hit the API again the moment someone else picks.
        self._global_cooldown_until: float = 0.0

    @property
    def cache(self) -> "diskcache.Cache":
        if self._cache is None:
            self._cache = diskcache.Cache(self._cache_dir)
        return self._cache

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def set_patch(self, patch: str) -> None:
        if patch and patch != self.patch:
            self.patch = patch

    def set_credentials(
        self, *, api_key: str | None, provider: str | None,
    ) -> None:
        """Update LLM credentials in place — used when the user saves a
        new API key / provider in Settings → API Keys. Avoids the
        rebuild-and-replay-state pattern that would drop ``patch`` and
        the in-flight task map."""
        self.api_key = api_key or ""
        if provider:
            self.provider = PROVIDERS.get(provider, PROVIDERS[DEFAULT_PROVIDER])
            self.model = self.provider.default_model

    # -- Sync surface ----------------------------------------------------

    def get_cached(
        self,
        *,
        champion: str,
        role: str,
        allies: list[str],
        enemies: list[str],
    ) -> str | None:
        """Return cached game plan or None — never hits the network."""
        key = self._cache_key(champion, role, allies, enemies)
        value = self.cache.get(key)
        if isinstance(value, str) and value:
            return value
        return None

    # -- Async surface ---------------------------------------------------

    async def prefetch(
        self,
        *,
        champion: str,
        role: str,
        allies: list[str],
        enemies: list[str],
    ) -> str | None:
        """Fetch + cache the game plan. No-op when no API key."""
        if not self.enabled or not champion:
            return None
        # Honor the global cooldown — once the provider 429/402'd we hold
        # off ALL signatures, not just the one that failed, because the
        # cache key changes on every team-comp tick during champ select.
        if _time.monotonic() < self._global_cooldown_until:
            return None
        cached = self.get_cached(
            champion=champion, role=role, allies=allies, enemies=enemies,
        )
        if cached is not None:
            return cached
        cache_key = self._cache_key(champion, role, allies, enemies)
        existing = self._inflight.get(cache_key)
        if existing is not None:
            return await existing
        task = asyncio.create_task(
            self._fetch(champion, role, allies, enemies, cache_key)
        )
        self._inflight[cache_key] = task
        try:
            return await task
        finally:
            self._inflight.pop(cache_key, None)

    async def _fetch(
        self,
        champion: str,
        role: str,
        allies: list[str],
        enemies: list[str],
        cache_key: str,
    ) -> str:
        ally_str = ", ".join(allies) or "(unknown)"
        enemy_str = ", ".join(enemies) or "(unknown)"
        prompt = (
            f"Champion: {champion}\n"
            f"Role: {role or 'unknown'}\n"
            f"Ally team: {ally_str}\n"
            f"Enemy team: {enemy_str}\n"
            "Write the game-plan paragraph."
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
                    "max_tokens": MAX_TOKENS,
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "game_plan_http_error provider=%s err=%s",
                self.provider.name, exc,
            )
            # Arm the global cooldown for any auth / quota / rate-limit
            # failure. 5 min is long enough to outlast a typical champ-
            # select churn so we don't keep paying for a doomed retry.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
                401, 402, 403, 429,
            ):
                self._global_cooldown_until = _time.monotonic() + 300.0
                self.cache.set(cache_key, "", expire=300)
            return ""
        except Exception:  # noqa: BLE001
            logger.exception("game_plan_unexpected_error provider=%s", self.provider.name)
            return ""

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            logger.warning("game_plan_response_shape_unexpected")
            return ""

        text = (content or "").strip()
        if not text:
            return ""
        self.cache.set(cache_key, text, expire=self.cache_ttl)
        logger.info(
            "game_plan_fetched champion=%s role=%s len=%d",
            champion, role, len(text),
        )
        return text

    # -- Helpers ---------------------------------------------------------

    def _cache_key(
        self,
        champion: str,
        role: str,
        allies: list[str],
        enemies: list[str],
    ) -> str:
        ally_h = hashlib.sha1(
            ",".join(sorted(filter(None, allies))).encode()
        ).hexdigest()[:8]
        enemy_h = hashlib.sha1(
            ",".join(sorted(filter(None, enemies))).encode()
        ).hexdigest()[:8]
        return f"game_plan:{self.patch}:{role}:{champion}:{ally_h}:{enemy_h}"

    async def aclose(self) -> None:
        if self._owns_client:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                logger.debug("game_plan_close_error")
        if self._cache is not None:
            self._cache.close()
