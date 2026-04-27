"""Tests for FixtureLcuSource and RealLcuSource."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from champ_assistant.lcu.events import LcuEventStream
from champ_assistant.lcu.lockfile import LockfileInfo
from champ_assistant.lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource


# --- Fake LcuClient / LcuEventStream factories ----------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class FakeLcuClient:
    """Stand-in for LcuClient — records GETs, replies from a script."""

    def __init__(self, get_responses: list[_FakeResponse | Exception] | None = None) -> None:
        self._get_responses = list(get_responses or [])
        self.gets: list[str] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "FakeLcuClient":
        self.entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True

    async def get(self, path: str, **kwargs: Any) -> _FakeResponse:
        self.gets.append(path)
        if not self._get_responses:
            return _FakeResponse(404)
        result = self._get_responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeEventStream:
    """Stand-in for LcuEventStream — yields scripted WS events."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self._events = list(events or [])
        self._closed = False
        self._cond = asyncio.Event()

    async def __aenter__(self) -> "FakeEventStream":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        self._cond.set()

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        for ev in self._events:
            if self._closed:
                return
            yield ev
        # After scripted events, hang until close() is called.
        await self._cond.wait()


def make_factories(
    *,
    get_responses: list[Any] | None = None,
    ws_events: list[dict[str, Any]] | None = None,
):
    """Return (client_factory, stream_factory, fake_client_ref, fake_stream_ref)."""
    state: dict[str, Any] = {"client": None, "stream": None}

    def client_factory(_lf: LockfileInfo) -> FakeLcuClient:
        c = FakeLcuClient(get_responses)
        state["client"] = c
        return c

    def stream_factory(_lf: LockfileInfo, _topics: list[str]) -> FakeEventStream:
        s = FakeEventStream(ws_events)
        state["stream"] = s
        return s

    return client_factory, stream_factory, state


def _session_event(phase: str = "BAN_PICK", event_type: str = "Update") -> dict[str, Any]:
    return {
        "topic": "OnJsonApiEvent_lol-champ-select_v1_session",
        "payload": {
            "data": {"phase": phase},
            "eventType": event_type,
            "uri": "/lol-champ-select/v1/session",
        },
    }


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
    cf, sf, _ = make_factories(get_responses=[_FakeResponse(404)])
    src = RealLcuSource(
        poll_interval=0.001,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[tmp_path / "lock"],
        client_factory=cf,
        stream_factory=sf,
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

    cf, sf, _ = make_factories(get_responses=[_FakeResponse(404)])
    src = RealLcuSource(
        poll_interval=0.01,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    async def remove_lockfile() -> None:
        await asyncio.sleep(0.05)
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


# ---------------------------------------------------------------------------
# RealLcuSource — live session streaming (REST + WS)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initial_get_yields_session_event(tmp_path: Path) -> None:
    """REST GET returns a session → emit it before subscribing."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    cf, sf, state = make_factories(
        get_responses=[_FakeResponse(200, {"phase": "BAN_PICK", "myTeam": []})],
    )
    src = RealLcuSource(
        poll_interval=0.05,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    received: list[dict[str, Any]] = []
    async for event in src.events():
        received.append(event)
        if event.get("type") == "session":
            await src.close()

    types = [e["type"] for e in received]
    assert "session" in types
    session = next(e for e in received if e["type"] == "session")
    assert session["data"] == {"phase": "BAN_PICK", "myTeam": []}
    # GET was made against the right path.
    assert state["client"].gets == ["/lol-champ-select/v1/session"]


@pytest.mark.asyncio
async def test_ws_update_event_yields_session(tmp_path: Path) -> None:
    """A WS Update event after the initial GET produces a session event."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    cf, sf, _ = make_factories(
        get_responses=[_FakeResponse(404)],  # not in champ select yet
        ws_events=[_session_event(phase="BAN_PICK", event_type="Update")],
    )
    src = RealLcuSource(
        poll_interval=0.05,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    received: list[dict[str, Any]] = []
    async for event in src.events():
        received.append(event)
        if event.get("type") == "session":
            await src.close()

    sessions = [e for e in received if e["type"] == "session"]
    assert len(sessions) == 1
    assert sessions[0]["data"]["phase"] == "BAN_PICK"


@pytest.mark.asyncio
async def test_ws_delete_yields_session_ended_without_disconnect(tmp_path: Path) -> None:
    """A WS Delete now soft-yields ``session_ended`` instead of tearing down
    the whole stream. The orchestrator keeps the WS connection alive and
    waits for the next session — no UI flicker between champ-select draws."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    cf, sf, _ = make_factories(
        get_responses=[_FakeResponse(404)],
        ws_events=[_session_event(event_type="Delete")],
    )
    src = RealLcuSource(
        poll_interval=0.01,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    received: list[str] = []
    async for event in src.events():
        received.append(event["type"])
        if "session_ended" in received:
            await src.close()
            break

    assert "session_ended" in received
    assert "connected" in received
    # No outer-loop reconnect happened — the stream stayed open, so we
    # should NOT see a disconnect bubble up from this event.
    assert "disconnected" not in received


@pytest.mark.asyncio
async def test_lockfile_vanishing_kills_stream(tmp_path: Path) -> None:
    """If the lockfile disappears mid-stream the watcher closes the stream."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    cf, sf, state = make_factories(
        get_responses=[_FakeResponse(404)],
        ws_events=[],  # stream hangs after init
    )
    src = RealLcuSource(
        poll_interval=0.02,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    async def consume() -> list[str]:
        result: list[str] = []
        async for event in src.events():
            result.append(event["type"])
            if event["type"] == "disconnected":
                await src.close()
                break
        return result

    consumer = asyncio.create_task(consume())
    # Wait for stream to start, then yank the lockfile.
    await asyncio.sleep(0.1)
    lock.unlink()
    received = await asyncio.wait_for(consumer, timeout=2.0)

    assert "connected" in received
    assert "disconnected" in received


@pytest.mark.asyncio
async def test_initial_get_500_is_swallowed_ws_still_works(tmp_path: Path) -> None:
    """REST 5xx errors don't kill the source — WS still subscribes."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    request = httpx.Request("GET", "https://127.0.0.1/x")
    cf, sf, _ = make_factories(
        get_responses=[
            httpx.HTTPStatusError(
                "500", request=request, response=httpx.Response(500, request=request)
            ),
        ],
        ws_events=[_session_event(phase="WS_BACKUP")],
    )
    # Wrap the httpx error in our LcuClientError type so the source's catch matches.
    from champ_assistant.lcu.client import LcuClientError
    cf2, sf2, _ = make_factories(
        get_responses=[LcuClientError("500 retries exhausted")],
        ws_events=[_session_event(phase="WS_BACKUP")],
    )
    src = RealLcuSource(
        poll_interval=0.05,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf2,
        stream_factory=sf2,
    )

    received: list[dict[str, Any]] = []
    async for event in src.events():
        received.append(event)
        if event.get("type") == "session":
            await src.close()

    sessions = [e for e in received if e["type"] == "session"]
    assert len(sessions) == 1
    assert sessions[0]["data"]["phase"] == "WS_BACKUP"


@pytest.mark.asyncio
async def test_close_during_streaming_exits_promptly(tmp_path: Path) -> None:
    """close() while inside the WS stream returns control within poll_interval."""
    lock = tmp_path / "lock"
    lock.write_text("LeagueClient:1:64144:abc:https", encoding="utf-8")

    cf, sf, _ = make_factories(
        get_responses=[_FakeResponse(404)],
        ws_events=[],  # hangs after init
    )
    src = RealLcuSource(
        poll_interval=0.02,
        platform="darwin",
        env={},
        home=tmp_path,
        extra=[lock],
        client_factory=cf,
        stream_factory=sf,
    )

    async def consume() -> int:
        n = 0
        async for event in src.events():
            n += 1
            if event["type"] == "connected":
                await src.close()
        return n

    n = await asyncio.wait_for(consume(), timeout=2.0)
    assert n >= 1
    assert src.closed is True
