"""Tests for TaskManager — track, auto-deregister, clean shutdown."""
from __future__ import annotations

import asyncio
import logging

import pytest

from champ_assistant.tasks import TaskManager


@pytest.mark.asyncio
async def test_spawn_tracks_task() -> None:
    tm = TaskManager()

    async def runner() -> None:
        await asyncio.sleep(0.05)

    task = tm.spawn(runner())
    assert tm.active_count == 1
    assert not task.done()
    await tm.shutdown()


@pytest.mark.asyncio
async def test_finished_task_auto_deregisters() -> None:
    tm = TaskManager()

    async def quick() -> int:
        return 42

    task = tm.spawn(quick())
    await task
    # Allow the done callback to run.
    await asyncio.sleep(0)
    assert tm.active_count == 0
    assert task.result() == 42


@pytest.mark.asyncio
async def test_shutdown_cancels_running_tasks() -> None:
    tm = TaskManager()
    started = asyncio.Event()

    async def slow() -> None:
        started.set()
        await asyncio.sleep(60)

    task = tm.spawn(slow())
    await started.wait()
    await tm.shutdown()
    assert task.cancelled()
    assert tm.active_count == 0
    assert tm.closed is True


@pytest.mark.asyncio
async def test_shutdown_when_no_tasks_is_noop() -> None:
    tm = TaskManager()
    await tm.shutdown()
    assert tm.closed is True


@pytest.mark.asyncio
async def test_spawn_after_shutdown_raises() -> None:
    tm = TaskManager()
    await tm.shutdown()

    async def noop() -> None:
        return None

    with pytest.raises(RuntimeError, match="closed"):
        tm.spawn(noop())


@pytest.mark.asyncio
async def test_failing_task_logs_but_does_not_crash_manager(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tm = TaskManager()

    async def boom() -> None:
        raise ValueError("boom")

    task = tm.spawn(boom(), name="boom-task")
    with caplog.at_level(logging.ERROR, logger="champ_assistant.tasks"):
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)

    assert any(rec.message == "task_failed" for rec in caplog.records)
    assert tm.active_count == 0
    await tm.shutdown()


@pytest.mark.asyncio
async def test_shutdown_idempotent_after_running_tasks_completed() -> None:
    tm = TaskManager()

    async def quick() -> None:
        return None

    tm.spawn(quick())
    await asyncio.sleep(0.01)
    await tm.shutdown()
    await tm.shutdown()
    assert tm.closed is True
    assert tm.active_count == 0
