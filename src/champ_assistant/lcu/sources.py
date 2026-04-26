"""LCU source abstraction: real client vs. fixture replay (dry-run).

Phase 2 module.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


class LcuSource(Protocol):
    """Common interface for live and fixture-driven event sources."""

    def events(self) -> AsyncIterator[dict[str, object]]: ...


class RealLcuSource:
    """Live LCU watcher: lockfile poll + REST + WebSocket."""

    def events(self) -> AsyncIterator[dict[str, object]]:
        raise NotImplementedError("Phase 2")


class FixtureLcuSource:
    """Replays JSON fixtures for dry-run / Mac development."""

    def __init__(
        self,
        fixture: Path | None = None,
        *,
        cycle: bool = False,
        stress: bool = False,
        interval: float = 5.0,
        rate: float = 10.0,
    ) -> None:
        self.fixture = fixture
        self.cycle = cycle
        self.stress = stress
        self.interval = interval
        self.rate = rate

    def events(self) -> AsyncIterator[dict[str, object]]:
        raise NotImplementedError("Phase 2")
