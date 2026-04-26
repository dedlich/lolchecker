"""Tests for FixtureLcuSource and RealLcuSource."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path

import pytest

from champ_assistant.lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource


def _write_session(path: Path, name: str, phase: str = "BAN_PICK") -> Path:
    f = path / name
    f.write_text(json.dumps({"phase": phase}), encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_protocol_conformance(tmp_path: Path) -> None:
    fix = _write_session(tmp_path, "x.json")
    src1 = FixtureLcuSource(fix)
    src2 = RealLcuSource()
    assert isinstance(src1, LcuSource)
    assert isinstance(src2, LcuSource)


# ---------------------------------------------------------------------------
# FixtureLcuSource
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fixture_single_file_yields_connected_then_session(tmp_path: Path) -> None:
    fix = _write_session(tmp_path, "s.json")
    src = FixtureLcuSource(fix, interval=0.0)
    received = [e async for e in src.events()]
    assert received[0] == {"type": "connected"}
    assert received[1] == {"type": "session", "data": {"phase": "BAN_PICK"}}


@pytest.mark.asyncio
async def test_fixture_directory_loads_all_in_sorted_order(tmp_path: Path) -> None:
    _write_session(tmp_path, "02.json", phase="PICK")
    _write_session(tmp_path, "01.json", phase="BAN")
    src = FixtureLcuSource(tmp_path, interval=0.0)
    received = [e async for e in src.events()]
    sessions = [e for e in received if e["type"] == "session"]
    assert [s["data"]["phase"] for s in sessions] == ["BAN", "PICK"]


@pytest.mark.asyncio
async def test_fixture_skips_corrupt_json_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_session(tmp_path, "good.json")
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    src = FixtureLcuSource(tmp_path, interval=0.0)
    with caplog.at_level(logging.WARNING, logger="champ_assistant.lcu.sources"):
        received = [e async for e in src.events()]
    sessions = [e for e in received if e["type"] == "session"]
    assert len(sessions) == 1
    assert any(rec.message == "fixture_invalid_json" for rec in caplog.records)


@pytest.mark.asyncio
async def test_fixture_missing_path_raises(tmp_path: Path) -> None:
    src = FixtureLcuSource(tmp_path / "nonexistent")
    with pytest.raises(FileNotFoundError):
        async for _ in src.events():
            pass


@pytest.mark.asyncio
async def test_fixture_directory_with_no_valid_files_raises(tmp_path: Path) -> None:
    (tmp_path / "garbage.json").write_text("{not json", encoding="utf-8")
    src = FixtureLcuSource(tmp_path)
    with pytest.raises(ValueError, match="No valid session"):
        async for _ in src.events():
            pass


@pytest.mark.asyncio
async def test_fixture_cycle_loops_until_closed(tmp_path: Path) -> None:
    _write_session(tmp_path, "a.json", phase="A")
    _write_session(tmp_path, "b.json", phase="B")
    src = FixtureLcuSource(tmp_path, cycle=True, interval=0.0)
    phases: list[str] = []
    async for event in src.events():
        if event["type"] == "session":
            phases.append(event["data"]["phase"])
            if len(phases) >= 5:
                await src.close()
    # Cycled at least one full pass.
    assert phases[:4] == ["A", "B", "A", "B"]


@pytest.mark.asyncio
async def test_fixture_stress_emits_at_high_rate(tmp_path: Path) -> None:
    _write_session(tmp_path, "a.json")
    _write_session(tmp_path, "b.json")
    rng = random.Random(42)
    src = FixtureLcuSource(tmp_path, stress=True, rate=1000.0, rng=rng)
    count = 0
    async for event in src.events():
        if event["type"] == "session":
            count += 1
            if count >= 20:
                await src.close()
    assert count >= 20


@pytest.mark.asyncio
async def test_fixture_close_during_default_iteration_stops(tmp_path: Path) -> None:
    _write_session(tmp_path, "a.json", phase="A")
    _write_session(tmp_path, "b.json", phase="B")
    _write_session(tmp_path, "c.json", phase="C")
    src = FixtureLcuSource(tmp_path, interval=0.0)
    seen: list[dict[str, object]] = []
    async for event in src.events():
        seen.append(event)
        if event.get("type") == "session" and event["data"]["phase"] == "A":
            await src.close()
    assert src.closed is True
    # Should not see B or C after close.
    phases = [e["data"]["phase"] for e in seen if e["type"] == "session"]
    assert phases == ["A"]


# ---------------------------------------------------------------------------
# RealLcuSource lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_real_source_yields_waiting_when_no_lockfile(tmp_path: Path) -> None:
    src = RealLcuSource(
        poll_interval=0.0, platform="darwin", env={}, home=tmp_path
    )
    seen = 0
    async for event in src.events():
        seen += 1
        assert event == {"type": "waiting_for_client"}
        if seen == 3:
            await src.close()
    assert seen == 3


@pytest.mark.asyncio
async def test_real_source_yields_connected_when_lockfile_appears(tmp_path: Path) -> None:
    src = RealLcuSource(
        poll_interval=0.001, platform="darwin", env={}, home=tmp_path, extra=[tmp_path / "lock"]
    )

    async def deliver_lockfile() -> None:
        await asyncio.sleep(0.02)
        (tmp_path / "lock").write_text(
            "LeagueClient:1:64144:abc:https", encoding="utf-8"
        )

    deliverer = asyncio.create_task(deliver_lockfile())
    received: list[str] = []
    async for event in src.events():
        received.append(event["type"])
        if event["type"] == "connected":
            await src.close()
    await deliverer
    assert "waiting_for_client" in received
    assert received[-1] == "connected"


@pytest.mark.asyncio
async def test_real_source_yields_disconnected_when_lockfile_removed(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    src = RealLcuSource(
        poll_interval=0.001, platform="darwin", env={}, home=tmp_path, extra=[lock]
    )

    async def remove_lockfile() -> None:
        await asyncio.sleep(0.02)
        lock.unlink()

    remover = asyncio.create_task(remove_lockfile())
    received: list[str] = []
    async for event in src.events():
        received.append(event["type"])
        if event["type"] == "disconnected":
            await src.close()
    await remover
    assert received[0] == "connected"
    assert "disconnected" in received


@pytest.mark.asyncio
async def test_real_source_corrupt_lockfile_yields_waiting(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lock = tmp_path / "lock"
    lock.write_text("garbage", encoding="utf-8")

    src = RealLcuSource(
        poll_interval=0.0, platform="darwin", env={}, home=tmp_path, extra=[lock]
    )
    seen: list[str] = []
    with caplog.at_level(logging.WARNING, logger="champ_assistant.lcu.sources"):
        async for event in src.events():
            seen.append(event["type"])
            if len(seen) == 2:
                await src.close()
    assert seen == ["waiting_for_client", "waiting_for_client"]
    assert any(rec.message == "lockfile_unparseable" for rec in caplog.records)


@pytest.mark.asyncio
async def test_real_source_close_exits_cleanly(tmp_path: Path) -> None:
    src = RealLcuSource(
        poll_interval=0.0, platform="darwin", env={}, home=tmp_path
    )

    async def consume() -> int:
        n = 0
        async for _ in src.events():
            n += 1
            if n >= 5:
                return n
        return n

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    await src.close()
    n = await asyncio.wait_for(task, timeout=1.0)
    assert n >= 1
    assert src.closed is True
