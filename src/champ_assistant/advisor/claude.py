"""Claude on-demand matchup explanations.

Phase 7: when the user clicks "AI Explain" on a counter or pick, we ask
Claude to write a focused 4-section matchup guide (lane phase, items,
mid-game, trades). This is opt-in — failures degrade gracefully (toast)
and never block champ select.

Design choices:
- ``anthropic.AsyncAnthropic`` (Claude API skill recommendation for async).
- Model: ``claude-haiku-4-5`` — sub-3s latency matters in champ select,
  Haiku 4.5 is the cheapest current model ($1/$5 per 1M) with plenty of
  capability for matchup explanations.
- ``diskcache`` for patch-stable matchup memoization (1 week TTL).
  Cache key includes the patch so a new game version invalidates everything.
- Anthropic prompt caching marker on the system block (``cache_control``):
  no-op until the system prompt crosses ~4096 tokens, but harmless and
  ready for when we add detailed champion data.
- Circuit breaker (masterplan §4.5): 3 consecutive failures → "open" for 5
  minutes. During the open window we skip the API call entirely. First
  success after cooldown closes the breaker.
- Every error path maps to a typed exception the UI can catch.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import diskcache

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"


_SYSTEM_PROMPT = """You are an expert League of Legends coach specializing in champion-select matchup explanations.

When a player asks how to play a matchup, you give a focused, actionable
4-section response — not whether the matchup is good or bad, but *how* to
win it once it's locked in.

Structure each response with these four headings, in order:

**Lane Phase**: Specific trading patterns levels 1–6. Reference power
spikes (level 2, 3, 6) and when to all-in vs. trade vs. play safe. Be
concrete about which ability to use first.

**Items**: 2–3 sentences on the core item path. What to rush given the
enemy matchup, what to skip. Boots choice if it's matchup-dependent.

**Mid-Game**: Item or level windows where the matchup shifts. When to
look for skirmishes, when to roam, when to wait.

**Trade Pattern**: One concrete trade. "Bait their X with Y, then engage
with Z."

Style rules:
- Refer to abilities by Q/W/E/R, not full names ("Bait his Q" not "Bait
  his Decisive Strike").
- 2–3 sentences per section. No fluff. No introduction or summary.
- Coaching tone — direct and opinionated. Players want decisions, not
  "it depends".
- Don't recommend bans, don't suggest other champions. The pick is locked.
- Don't use marketing language ("powerful", "amazing", "legendary").
- Assume the player knows their own champion's basic kit. Focus on the matchup."""


# --- Exceptions -----------------------------------------------------------


class ClaudeAdvisorError(Exception):
    """Base class for ClaudeAdvisor failures."""


class CircuitBreakerOpen(ClaudeAdvisorError):
    """The circuit breaker is open; the feature is in cooldown."""


class ClaudeApiError(ClaudeAdvisorError):
    """API call failed (network, rate limit, auth, parse). UI shows a toast."""


# --- Circuit breaker ------------------------------------------------------


@dataclass
class _CircuitBreakerState:
    consecutive_failures: int = 0
    opened_at: float | None = None

    def is_open(self, cooldown_seconds: float, now: float) -> bool:
        if self.opened_at is None:
            return False
        return (now - self.opened_at) < cooldown_seconds

    def remaining_cooldown(self, cooldown_seconds: float, now: float) -> float:
        if self.opened_at is None:
            return 0.0
        return max(0.0, cooldown_seconds - (now - self.opened_at))

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, threshold: int, now: float) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= threshold:
            self.opened_at = now


# --- Advisor --------------------------------------------------------------


class ClaudeAdvisor:
    """On-demand AI matchup explanations with caching + circuit breaker."""

    DEFAULT_MAX_TOKENS = 512
    DEFAULT_TIMEOUT = 15.0  # masterplan §4.5
    DEFAULT_CACHE_TTL = 7 * 24 * 60 * 60  # 1 week — matchup is patch-stable
    DEFAULT_FAILURE_THRESHOLD = 3
    DEFAULT_COOLDOWN_SECONDS = 5 * 60  # 5 minutes

    def __init__(
        self,
        cache_dir: Path,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        client: Any | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.cache = diskcache.Cache(str(cache_dir))
        self.model = model
        self.max_tokens = max_tokens
        self.cache_ttl = cache_ttl
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._owns_client = client is None
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout
        )
        self._breaker = _CircuitBreakerState()
        self._lock = asyncio.Lock()
        self._now = clock

    @property
    def breaker_state(self) -> _CircuitBreakerState:
        """Read-only access for tests / diagnostics."""
        return self._breaker

    async def explain_matchup(
        self,
        enemy_key: str,
        my_pick_key: str,
        role: str,
        *,
        patch: str | None = None,
    ) -> str:
        """Return how to play ``my_pick_key`` against ``enemy_key`` in ``role``.

        Raises:
          CircuitBreakerOpen: feature is in cooldown after repeated failures
          ClaudeApiError:     API call failed (UI should show a toast)
        """
        if not enemy_key or not my_pick_key or not role:
            raise ValueError("enemy_key, my_pick_key, and role are required")

        cache_key = self._cache_key(enemy_key, my_pick_key, role, patch)
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._lock:
            now = self._now()
            if self._breaker.is_open(self.cooldown_seconds, now):
                remaining = self._breaker.remaining_cooldown(
                    self.cooldown_seconds, now
                )
                raise CircuitBreakerOpen(
                    f"Claude advisor is in cooldown for {remaining:.0f}s"
                )

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"I'm playing **{my_pick_key}** {role} against "
                            f"**{enemy_key}** on patch {patch or 'current'}. "
                            "How do I play this matchup?"
                        ),
                    }
                ],
            )
        except anthropic.APIError as exc:
            await self._record_failure()
            logger.warning(
                "claude_api_error",
                extra={
                    "error_type": type(exc).__name__,
                    "matchup": f"{my_pick_key}_vs_{enemy_key}_{role}",
                },
            )
            raise ClaudeApiError(f"Claude API failed: {exc}") from exc
        except Exception as exc:
            await self._record_failure()
            logger.exception("claude_unexpected_error")
            raise ClaudeApiError(f"Unexpected failure: {exc}") from exc

        try:
            text = self._extract_text(response)
        except (AttributeError, TypeError) as exc:
            await self._record_failure()
            raise ClaudeApiError(f"Could not parse Claude response: {exc}") from exc

        if not text:
            await self._record_failure()
            raise ClaudeApiError("Claude returned an empty response")

        await self._record_success()
        self.cache.set(cache_key, text, expire=self.cache_ttl)
        return text

    async def aclose(self) -> None:
        if self._owns_client:
            try:
                await self._client.close()
            except Exception:
                logger.debug("anthropic_client_close_error")
        self.cache.close()

    # -- Helpers ---------------------------------------------------------

    @staticmethod
    def _extract_text(response: Any) -> str:
        parts: list[str] = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts).strip()

    @staticmethod
    def _cache_key(enemy: str, mine: str, role: str, patch: str | None) -> str:
        raw = f"{patch or 'unknown'}|{role}|{mine}|{enemy}"
        return "claude:matchup:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def _record_success(self) -> None:
        async with self._lock:
            self._breaker.record_success()

    async def _record_failure(self) -> None:
        async with self._lock:
            self._breaker.record_failure(self.failure_threshold, self._now())
