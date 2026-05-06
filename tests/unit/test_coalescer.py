"""Tests for Coalescer — async fan-out deduplication helper."""
from __future__ import annotations

import asyncio

import pytest

from champ_assistant.coalescer import Coalescer


# ---------------------------------------------------------------------------
# Basic scheduling + dedup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_call_schedules() -> None:
    c: Coalescer[str] = Coalescer()
    finished = asyncio.Event()

    async def fetch() -> None:
        finished.set()

    assert c.schedule("k", fetch) is True
    # Wait for the wrapped task to complete.
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    # Allow the wrapper's finally to run.
    await asyncio.sleep(0)
    assert c.is_inflight("k") is False


@pytest.mark.asyncio
async def test_second_call_for_same_key_is_skipped() -> None:
    c: Coalescer[str] = Coalescer()
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def fetch() -> None:
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()

    # First call schedules.
    assert c.schedule("k", fetch) is True
    await asyncio.wait_for(started.wait(), timeout=1.0)
    # Second call while the first is in-flight is skipped.
    assert c.schedule("k", fetch) is False
    # Let the first one finish.
    release.set()
    await asyncio.sleep(0)  # let task run
    await asyncio.sleep(0)  # let wrapper's finally run
    assert call_count == 1


@pytest.mark.asyncio
async def test_different_keys_both_schedule() -> None:
    c: Coalescer[str] = Coalescer()
    counter = 0

    async def fetch() -> None:
        nonlocal counter
        counter += 1

    assert c.schedule("a", fetch) is True
    assert c.schedule("b", fetch) is True
    # Drain pending tasks.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert counter == 2


# ---------------------------------------------------------------------------
# Discard guarantees — completion, exception, RuntimeError-on-no-loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discard_after_successful_completion() -> None:
    c: Coalescer[str] = Coalescer()

    async def fetch() -> None:
        pass

    c.schedule("k", fetch)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert c.is_inflight("k") is False
    assert c.inflight_count() == 0


@pytest.mark.asyncio
async def test_discard_after_exception_in_factory() -> None:
    """Even if the user's coroutine raises, the key must be discarded."""
    c: Coalescer[str] = Coalescer()

    async def fetch() -> None:
        raise RuntimeError("boom")

    c.schedule("k", fetch)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert c.is_inflight("k") is False


@pytest.mark.asyncio
async def test_re_schedule_after_completion() -> None:
    """Once the first task completes, a same-key call schedules again."""
    c: Coalescer[str] = Coalescer()
    call_count = 0

    async def fetch() -> None:
        nonlocal call_count
        call_count += 1

    assert c.schedule("k", fetch) is True
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Now re-schedule — first one is done.
    assert c.schedule("k", fetch) is True
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert call_count == 2


def test_schedule_without_running_loop_returns_false() -> None:
    """Sync call path (no event loop) — schedule must return False
    without leaking the key into the inflight set."""
    c: Coalescer[str] = Coalescer()

    async def fetch() -> None:  # never called
        pass

    assert c.schedule("k", fetch) is False
    assert c.is_inflight("k") is False
    assert c.inflight_count() == 0


# ---------------------------------------------------------------------------
# Tuple keys (the runtime_counters use case)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supports_tuple_keys() -> None:
    """Real call site uses ``(enemy_key, role)`` tuples — verify Hashable
    keys other than str work."""
    c: Coalescer[tuple[str, str]] = Coalescer()
    seen: list[tuple[str, str]] = []

    async def fetch_a() -> None:
        seen.append(("Yasuo", "MID"))

    async def fetch_b() -> None:
        seen.append(("Jinx", "BOT"))

    c.schedule(("Yasuo", "MID"), fetch_a)
    c.schedule(("Jinx", "BOT"), fetch_b)
    c.schedule(("Yasuo", "MID"), fetch_a)  # dedup'd
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sorted(seen) == [("Jinx", "BOT"), ("Yasuo", "MID")]


# ---------------------------------------------------------------------------
# Inflight-count for diagnostics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inflight_count_tracks_pending() -> None:
    c: Coalescer[str] = Coalescer()
    release = asyncio.Event()

    async def fetch() -> None:
        await release.wait()

    c.schedule("a", fetch)
    c.schedule("b", fetch)
    c.schedule("c", fetch)
    assert c.inflight_count() == 3
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert c.inflight_count() == 0
