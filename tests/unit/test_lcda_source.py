"""Tests for the polling LCDA source."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from champ_assistant.lcda.client import LcdaClient
from champ_assistant.lcda.source import LcdaSnapshot, LcdaSource, _extract_game_result

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "lcda"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.mark.asyncio
async def test_snapshot_emitted_with_objectives() -> None:
    payload = _load("allgamedata_midgame.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = LcdaClient(transport=httpx.MockTransport(handler))
    received: list[LcdaSnapshot | None] = []

    async def cb(snap: LcdaSnapshot | None) -> None:
        received.append(snap)

    source = LcdaSource(client, cb, poll_interval=0.0)
    await source._tick()
    await client.aclose()

    assert len(received) == 1
    snap = received[0]
    assert snap is not None
    assert snap.game_mode == "CLASSIC"
    assert snap.game_time == 1620.0
    names = {o.name for o in snap.objectives}
    assert names == {"VoidGrubs", "Dragon", "Baron", "Herald"}
    drag = next(o for o in snap.objectives if o.name == "Dragon")
    assert drag.last_killed_seconds == 1100.0


@pytest.mark.asyncio
async def test_unreachable_emits_none_after_stale_window() -> None:
    state = {"alive": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["alive"]:
            return httpx.Response(200, json=_load("allgamedata_early.json"))
        raise httpx.ConnectError("game ended")

    client = LcdaClient(transport=httpx.MockTransport(handler))
    received: list[LcdaSnapshot | None] = []

    async def cb(snap: LcdaSnapshot | None) -> None:
        received.append(snap)

    fake_clock = {"t": 0.0}

    def clock() -> float:
        return fake_clock["t"]

    source = LcdaSource(
        client, cb, poll_interval=0.0, stale_after=5.0, clock=clock
    )

    # First tick: alive
    await source._tick()
    assert received[-1] is not None

    # Game ends; not stale yet → no callback transition
    state["alive"] = False
    fake_clock["t"] = 1.0
    await source._tick()
    assert len(received) == 1  # still no None — within stale window

    # Past the stale window → emits None
    fake_clock["t"] = 10.0
    await source._tick()
    assert received[-1] is None
    await client.aclose()


@pytest.mark.asyncio
async def test_run_loops_until_close() -> None:
    payload = _load("allgamedata_early.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = LcdaClient(transport=httpx.MockTransport(handler))
    counter = {"n": 0}

    async def cb(snap: LcdaSnapshot | None) -> None:
        counter["n"] += 1

    source = LcdaSource(client, cb, poll_interval=0.0)
    task = asyncio.create_task(source.run())
    await asyncio.sleep(0.05)
    source.close()
    await asyncio.wait_for(task, timeout=1.0)
    await client.aclose()
    assert counter["n"] >= 1


# ----------------------------------------------------------------------
# _extract_game_result — game-end detection
# ----------------------------------------------------------------------

def test_extract_game_result_win() -> None:
    events = [{"EventName": "GameEnd", "EventTime": 1800.0, "Result": "Win"}]
    assert _extract_game_result(events) == "Win"


def test_extract_game_result_lose() -> None:
    events = [
        {"EventName": "DragonKill", "EventTime": 300.0},
        {"EventName": "GameEnd", "EventTime": 1800.0, "Result": "Lose"},
    ]
    assert _extract_game_result(events) == "Lose"


def test_extract_game_result_empty_when_no_game_end() -> None:
    events = [
        {"EventName": "DragonKill", "EventTime": 300.0},
        {"EventName": "BaronKill", "EventTime": 900.0},
    ]
    assert _extract_game_result(events) == ""


def test_extract_game_result_empty_list() -> None:
    assert _extract_game_result([]) == ""


@pytest.mark.asyncio
async def test_snapshot_carries_game_result_win() -> None:
    payload = _load("allgamedata_midgame.json")
    import copy
    payload = copy.deepcopy(payload)
    payload.setdefault("events", {}).setdefault("Events", []).append(
        {"EventName": "GameEnd", "EventTime": 1800.0, "Result": "Win"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = LcdaClient(transport=httpx.MockTransport(handler))
    received: list[LcdaSnapshot | None] = []

    async def cb(snap: LcdaSnapshot | None) -> None:
        received.append(snap)

    source = LcdaSource(client, cb, poll_interval=0.0)
    await source._tick()
    await client.aclose()

    snap = received[0]
    assert snap is not None
    assert snap.game_result == "Win"


@pytest.mark.asyncio
async def test_snapshot_game_result_empty_without_game_end_event() -> None:
    payload = _load("allgamedata_midgame.json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = LcdaClient(transport=httpx.MockTransport(handler))
    received: list[LcdaSnapshot | None] = []

    async def cb(snap: LcdaSnapshot | None) -> None:
        received.append(snap)

    source = LcdaSource(client, cb, poll_interval=0.0)
    await source._tick()
    await client.aclose()

    snap = received[0]
    assert snap is not None
    assert snap.game_result == ""
