"""LCU event source abstraction.

A source produces an async stream of lifecycle + data events shaped like::

    {"type": "waiting_for_client"}      # lockfile not found
    {"type": "connected"}                # lockfile parsed; client reachable
    {"type": "disconnected"}             # lockfile vanished
    {"type": "session", "data": {...}}   # champ-select session payload

Two implementations:
  - ``RealLcuSource``    — polls lockfile, surfaces lifecycle. Wiring of the
                           REST+WS event stream lands in Phase 6 (Integration).
  - ``FixtureLcuSource`` — replays JSON fixtures, masterplan §5.6 dry-run.

Common interface lets ``__main__.py`` swap the source via ``--dry-run``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .lockfile import LockfileError, LockfileNotFound, find_lockfile, parse_lockfile

logger = logging.getLogger(__name__)


@runtime_checkable
class LcuSource(Protocol):
    def events(self) -> AsyncIterator[dict[str, Any]]: ...
    async def close(self) -> None: ...


class FixtureLcuSource:
    """Replays JSON champ-select session fixtures.

    Modes:
      - default: yield each session once with ``interval`` seconds between, then stop
      - cycle:   loop the fixture list forever
      - stress:  cycle at ``rate`` Hz, picking random fixture index each tick

    The ``fixture`` argument may be a file (single session) or a directory
    (all ``*.json`` files, sorted by filename).
    """

    def __init__(
        self,
        fixture: Path,
        *,
        cycle: bool = False,
        stress: bool = False,
        interval: float = 5.0,
        rate: float = 10.0,
        rng: random.Random | None = None,
    ) -> None:
        self.fixture_path = Path(fixture)
        self.cycle = cycle
        self.stress = stress
        self.interval = interval
        self.rate = rate
        self._rng = rng or random.Random()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True

    def _load(self) -> list[dict[str, Any]]:
        path = self.fixture_path
        if path.is_dir():
            files = sorted(path.glob("*.json"))
        elif path.is_file():
            files = [path]
        else:
            raise FileNotFoundError(f"Fixture path does not exist: {path}")

        sessions: list[dict[str, Any]] = []
        for f in files:
            try:
                sessions.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "fixture_invalid_json", extra={"path": str(f), "err": str(exc)}
                )
        if not sessions:
            raise ValueError(f"No valid session fixtures at {path}")
        return sessions

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        sessions = self._load()
        yield {"type": "connected"}

        if self.stress:
            sleep_for = max(1.0 / self.rate, 0.001)
            while not self._closed:
                idx = self._rng.randrange(len(sessions))
                yield {"type": "session", "data": sessions[idx]}
                if not await self._sleep_unless_closed(sleep_for):
                    return
            return

        if self.cycle:
            i = 0
            while not self._closed:
                yield {"type": "session", "data": sessions[i]}
                i = (i + 1) % len(sessions)
                if not await self._sleep_unless_closed(self.interval):
                    return
            return

        for session in sessions:
            if self._closed:
                return
            yield {"type": "session", "data": session}
            if not await self._sleep_unless_closed(self.interval):
                return

    async def _sleep_unless_closed(self, seconds: float) -> bool:
        try:
            await asyncio.sleep(max(seconds, 0))
        except asyncio.CancelledError:
            return False
        return not self._closed


class RealLcuSource:
    """Polls the lockfile, surfaces lifecycle events.

    Phase 2d ships the lifecycle layer only (waiting / connected /
    disconnected). Phase 6 (Integration) plugs ``LcuClient`` + ``LcuEventStream``
    in to emit ``{"type": "session", ...}`` events from a live client.
    """

    DEFAULT_POLL_INTERVAL = 1.0

    def __init__(
        self,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        platform: str | None = None,
        env: dict[str, str] | None = None,
        home: Path | None = None,
        extra: list[Path] | None = None,
    ) -> None:
        self.poll_interval = poll_interval
        self._platform = platform
        self._env = env
        self._home = home
        self._extra = extra
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        was_connected = False
        while not self._closed:
            try:
                lockfile_path = find_lockfile(
                    platform=self._platform,
                    env=self._env,
                    home=self._home,
                    extra=self._extra,
                )
                # Validate parseability before claiming "connected".
                parse_lockfile(lockfile_path)
            except LockfileNotFound:
                if was_connected:
                    yield {"type": "disconnected"}
                    was_connected = False
                yield {"type": "waiting_for_client"}
                if not await self._sleep_unless_closed(self.poll_interval):
                    return
                continue
            except LockfileError as exc:
                # Mid-write corruption: surface as "waiting" so the UI shows
                # the same state as a missing lockfile (both mean "client not
                # ready"). Logging captures the parse error for debugging.
                logger.warning("lockfile_unparseable", extra={"error": str(exc)})
                if was_connected:
                    yield {"type": "disconnected"}
                    was_connected = False
                yield {"type": "waiting_for_client"}
                if not await self._sleep_unless_closed(self.poll_interval):
                    return
                continue

            if not was_connected:
                yield {"type": "connected"}
                was_connected = True

            # Phase 6: open LcuClient + LcuEventStream here and yield
            # {"type": "session", "data": ...} events. For now we just monitor
            # the lockfile so the lifecycle is observable.
            while not self._closed and lockfile_path.is_file():
                if not await self._sleep_unless_closed(self.poll_interval):
                    return

    async def _sleep_unless_closed(self, seconds: float) -> bool:
        try:
            await asyncio.sleep(max(seconds, 0))
        except asyncio.CancelledError:
            return False
        return not self._closed
