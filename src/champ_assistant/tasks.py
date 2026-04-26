"""Async task lifecycle management.

Phase 0 skeleton. Phase 1 wires up TaskManager with tests.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class TaskManager:
    """Tracks spawned asyncio tasks for clean shutdown.

    Real implementation (Phase 1):
      - spawn(coro) → asyncio.Task, registered for tracking
      - tasks self-deregister via add_done_callback
      - shutdown() cancels and awaits all
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        raise NotImplementedError("Phase 1")

    async def shutdown(self) -> None:
        raise NotImplementedError("Phase 1")
