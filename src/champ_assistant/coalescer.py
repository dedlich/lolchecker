"""Async fan-out deduplication helper.

The orchestrator schedules background fetches keyed on identifiers
(profile-by-puuid, runtime-counter-by-(champ,role), etc.). Without
deduplication, every snapshot tick re-schedules the same fetch — wasting
API calls and racing against the disk cache.

The naive pattern is a hand-rolled ``set`` per fetch type:

    if key in self._inflight:
        return
    self._inflight.add(key)
    try:
        loop.create_task(self._fetch(key, ...))
    except RuntimeError:
        self._inflight.discard(key)  # easy to forget

    async def _fetch(self, key, ...):
        try:
            ...
        finally:
            self._inflight.discard(key)  # easy to forget

The discard appears in three places (RuntimeError path + task finally +
explicit completion); forgetting one leaves the key permanently
"inflight" and the fetch never re-runs.

``Coalescer`` centralizes the bookkeeping. The caller hands in a
factory that produces the coroutine; the helper schedules it only if
the key isn't already inflight, and guarantees discard via a wrapping
``finally`` block. One discard, one place.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Generic, Hashable, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K", bound=Hashable)


class Coalescer(Generic[K]):
    """Deduplicates async fan-outs by hashable key.

    Usage::

        self._fetcher = Coalescer[tuple[str, str]]()

        def request_counters(self, champ: str, role: str) -> None:
            self._fetcher.schedule(
                (champ, role),
                lambda: self._fetch_and_rerender(champ, role),
            )

        async def _fetch_and_rerender(self, champ: str, role: str) -> None:
            counters = await self._store.get(champ, role)
            ...
            # No need to remember to discard the key — Coalescer's
            # wrapping finally handles it.

    Thread / loop safety: scheduling is a synchronous bookkeeping op
    on the single event loop. Concurrent calls from coroutines on the
    same loop are safe (no preemption between the membership check
    and the add). Cross-loop / cross-thread use is out of scope.
    """

    def __init__(self) -> None:
        self._inflight: set[K] = set()

    def is_inflight(self, key: K) -> bool:
        """True if a fetch for ``key`` is currently scheduled."""
        return key in self._inflight

    def inflight_count(self) -> int:
        """Number of keys currently scheduled. Useful for diagnostics."""
        return len(self._inflight)

    def schedule(
        self,
        key: K,
        factory: Callable[[], Awaitable[None]],
    ) -> bool:
        """Schedule an async task if no fetch for ``key`` is already inflight.

        Returns ``True`` if a new task was created, ``False`` if either
        the key is already inflight or no event loop is running (e.g. a
        sync test path). In both False cases ``factory`` is NOT called,
        so it can safely allocate state cheaply on every call.

        The wrapping coroutine guarantees ``key`` is discarded once the
        underlying task completes — exception or otherwise. Caller's
        coroutine is free to do its own error handling without
        needing to remember the discard.
        """
        if key in self._inflight:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not inside an event loop (sync test path, pre-startup).
            # Don't add the key — the task can never complete to discard it.
            return False
        self._inflight.add(key)
        loop.create_task(self._wrap(key, factory))
        return True

    async def _wrap(
        self,
        key: K,
        factory: Callable[[], Awaitable[None]],
    ) -> None:
        """Run the user's coroutine; guarantee the discard runs even on
        exception. Logging is intentionally minimal — the user's task
        owns its own error reporting."""
        try:
            await factory()
        except Exception:  # noqa: BLE001 — user task handles its own errors
            logger.exception("coalescer_task_failed key=%r", key)
        finally:
            self._inflight.discard(key)
