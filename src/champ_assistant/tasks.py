"""Async task lifecycle management.

The app spawns many background tasks (LCU watcher, WS listener, debounced
UI refreshers). On shutdown we must cancel them all deterministically —
otherwise we leak coroutines and Python prints noisy ``Task was destroyed
but it is pending`` warnings.

Usage::

    tm = TaskManager()
    tm.spawn(watch_lcu())
    ...
    await tm.shutdown()
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)


class TaskManager:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    @property
    def closed(self) -> bool:
        return self._closed

    def spawn(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        if self._closed:
            coro.close()
            raise RuntimeError("TaskManager is closed; cannot spawn new tasks.")

        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_done)
        return task

    def _on_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None and not isinstance(exc, asyncio.CancelledError):
            logger.error(
                "task_failed",
                extra={"task": task.get_name(), "error": repr(exc)},
            )

    async def shutdown(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
