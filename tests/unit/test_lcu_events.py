"""Tests for LcuEventStream — subscribe, filter, reconnect, clean shutdown.

Uses an injected fake websocket factory so we don't need a live server.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close

from champ_assistant.lcu.events import LcuEventStream
from champ_assistant.lcu.lockfile import LockfileInfo

LOCKFILE = LockfileInfo("LeagueClient", 1, 64144, "abc", "https")
TOPIC = "OnJsonApiEvent_lol-champ-select_v1_session"
OTHER_TOPIC = "OnJsonApiEvent_lol-summoner_v1_current-summoner"


def _closed_exc() -> ConnectionClosed:
    rcvd = Close(code=1000, reason="bye")
    return ConnectionClosed(rcvd=rcvd, sent=None)


class FakeWebSocket:
    """Fake websocket: records sent frames; yields queued raw messages then ends."""

    def __init__(
        self,
        incoming: list[str] | None = None,
        *,
        end: str = "close",  # "close" → ConnectionClosed, "stop" → StopAsyncIteration
    ) -> None:
        self.sent: list[str] = []
        self.closed = False
        self._incoming = list(incoming or [])
        self._end = end

    async def send(self, message: str) -> None:
        if self.closed:
            raise _closed_exc()
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for item in self._incoming:
            if self.closed:
                return
            yield item
        if self._end == "close":
            raise _closed_exc()
        # else: just stop


def _event_frame(topic: str, payload: dict[str, object]) -> str:
    return json.dumps([8, topic, payload])


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_empty_subscriptions_raises() -> None:
    with pytest.raises(ValueError, match="subscription"):
        LcuEventStream(LOCKFILE, [])


# ---------------------------------------------------------------------------
# Subscribe + receive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_frame_is_sent() -> None:
    fake = FakeWebSocket(incoming=[], end="close")

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(
        LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0, reconnect_factor=1.0
    )
    # Drain a couple of reconnects then close
    async with stream:
        async def consume() -> None:
            async for _ in stream:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await stream.close()
        await task

    assert fake.sent == [json.dumps([5, TOPIC])]


@pytest.mark.asyncio
async def test_yields_matching_event() -> None:
    fake = FakeWebSocket(
        incoming=[_event_frame(TOPIC, {"data": {"foo": 1}})],
        end="close",
    )

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0)
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                await stream.close()
                return

        await asyncio.wait_for(consume(), timeout=1.0)

    assert len(received) == 1
    assert received[0]["topic"] == TOPIC
    assert received[0]["payload"] == {"data": {"foo": 1}}


@pytest.mark.asyncio
async def test_filters_unsubscribed_topic() -> None:
    fake = FakeWebSocket(
        incoming=[
            _event_frame(OTHER_TOPIC, {"data": "ignored"}),
            _event_frame(TOPIC, {"data": "kept"}),
        ],
        end="close",
    )

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0)
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                await stream.close()
                return

        await asyncio.wait_for(consume(), timeout=1.0)

    assert len(received) == 1
    assert received[0]["payload"] == {"data": "kept"}


@pytest.mark.asyncio
async def test_drops_malformed_frames() -> None:
    fake = FakeWebSocket(
        incoming=[
            "not json",
            "{}",  # not a list
            "[8]",  # too short
            "[5, \"topic\"]",  # subscribe opcode, not event
            json.dumps([8, TOPIC, "not-a-dict"]),
            _event_frame(TOPIC, {"ok": True}),
        ],
        end="close",
    )

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0)
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                await stream.close()
                return

        await asyncio.wait_for(consume(), timeout=1.0)
    assert len(received) == 1
    assert received[0]["payload"] == {"ok": True}


@pytest.mark.asyncio
async def test_handles_bytes_payload() -> None:
    raw = _event_frame(TOPIC, {"hello": "world"}).encode("utf-8")
    fake = FakeWebSocket(incoming=[raw], end="close")  # type: ignore[arg-type]

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0)
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                await stream.close()
                return

        await asyncio.wait_for(consume(), timeout=1.0)
    assert received[0]["payload"] == {"hello": "world"}


# ---------------------------------------------------------------------------
# Reconnect behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconnects_after_disconnect() -> None:
    """First connection drops after one event; second yields another event."""
    sockets = [
        FakeWebSocket(incoming=[_event_frame(TOPIC, {"n": 1})], end="close"),
        FakeWebSocket(incoming=[_event_frame(TOPIC, {"n": 2})], end="close"),
    ]
    iter_sockets = iter(sockets)

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return next(iter_sockets)

    stream = LcuEventStream(
        LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0, reconnect_factor=1.0
    )
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                if len(received) == 2:
                    await stream.close()
                    return

        await asyncio.wait_for(consume(), timeout=1.0)

    assert [e["payload"]["n"] for e in received] == [1, 2]
    # Both sockets each received one subscribe.
    assert sockets[0].sent == [json.dumps([5, TOPIC])]
    assert sockets[1].sent == [json.dumps([5, TOPIC])]


@pytest.mark.asyncio
async def test_reconnects_after_connect_failure() -> None:
    """First connect attempt raises; second succeeds."""
    success = FakeWebSocket(incoming=[_event_frame(TOPIC, {"hello": True})], end="close")
    attempts = {"n": 0}

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("connect refused")
        return success

    stream = LcuEventStream(
        LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0, reconnect_factor=1.0
    )
    received: list[dict[str, object]] = []
    async with stream:
        async def consume() -> None:
            async for event in stream:
                received.append(event)
                await stream.close()
                return

        await asyncio.wait_for(consume(), timeout=1.0)

    assert len(received) == 1
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_close_during_iteration_exits_cleanly() -> None:
    """close() while async-for is running should drain quickly without errors."""
    # Provide infinite-ish messages so iteration is busy when we close.
    fake = FakeWebSocket(
        incoming=[_event_frame(TOPIC, {"n": i}) for i in range(50)],
        end="stop",
    )

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    stream = LcuEventStream(LOCKFILE, [TOPIC], connect=factory, reconnect_initial=0.0)
    seen = 0
    async with stream:
        async for _ in stream:
            seen += 1
            if seen == 3:
                await stream.close()
    assert stream.closed is True
    assert fake.closed is True


@pytest.mark.asyncio
async def test_multiple_subscriptions_each_send_subscribe() -> None:
    fake = FakeWebSocket(incoming=[], end="close")

    async def factory(_lf: LockfileInfo) -> FakeWebSocket:
        return fake

    topics = [TOPIC, OTHER_TOPIC]
    stream = LcuEventStream(LOCKFILE, topics, connect=factory, reconnect_initial=0.0)
    async with stream:
        async def consume() -> None:
            async for _ in stream:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await stream.close()
        await task

    assert fake.sent == [json.dumps([5, t]) for t in topics]
