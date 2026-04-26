"""LCU WebSocket subscriber with reconnect + heartbeat.

Phase 2 module.
"""
from __future__ import annotations


class LcuEventStream:
    """Async iterator over filtered LCU JSON events."""

    async def __aiter__(self) -> "LcuEventStream":
        raise NotImplementedError("Phase 2")

    async def __anext__(self) -> dict[str, object]:
        raise NotImplementedError("Phase 2")
