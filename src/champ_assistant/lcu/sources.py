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
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .client import LcuClient, LcuClientError
from .events import LcuEventStream
from .lockfile import (
    LockfileError,
    LockfileInfo,
    LockfileNotFound,
    find_lockfile,
    parse_lockfile,
)

CHAMP_SELECT_REST_PATH = "/lol-champ-select/v1/session"
CHAMP_SELECT_WS_TOPIC = "OnJsonApiEvent_lol-champ-select_v1_session"

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


ClientFactory = Callable[[LockfileInfo], LcuClient]
StreamFactory = Callable[[LockfileInfo, list[str]], LcuEventStream]


def _default_client_factory(lockfile: LockfileInfo) -> LcuClient:
    return LcuClient(lockfile)


def _default_stream_factory(lockfile: LockfileInfo, topics: list[str]) -> LcuEventStream:
    return LcuEventStream(lockfile, topics)


class RealLcuSource:
    """Live LCU watcher: lockfile poll → REST initial state → WS event stream.

    Outer loop: detect the League client via the lockfile and surface
    waiting / connected / disconnected lifecycle events.

    Inner loop (when connected): open an :class:`LcuClient`, GET the current
    champ-select session for the initial snapshot, then subscribe to
    ``OnJsonApiEvent_lol-champ-select_v1_session`` via :class:`LcuEventStream`
    for live updates. A concurrent lockfile watcher closes the stream as soon
    as the client process exits.

    LcuClient and LcuEventStream are constructed via injectable factories
    so unit tests can substitute fakes without standing up a real LCU server.
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
        client_factory: ClientFactory = _default_client_factory,
        stream_factory: StreamFactory = _default_stream_factory,
        process_iter: Any = None,
    ) -> None:
        self.poll_interval = poll_interval
        self._platform = platform
        self._env = env
        self._home = home
        self._extra = extra
        self._client_factory = client_factory
        self._stream_factory = stream_factory
        self._process_iter = process_iter
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
                    process_iter=self._process_iter,
                )
                lockfile_info = parse_lockfile(lockfile_path)
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

            try:
                async for event in self._stream_sessions(lockfile_info, lockfile_path):
                    if self._closed:
                        return
                    yield event
            except Exception as exc:
                # Anything unexpected from the stream: log + drop back to
                # the outer reconnect loop. The stream is responsible for
                # retrying transient WS issues itself.
                logger.warning(
                    "lcu_stream_failed",
                    extra={"error_type": type(exc).__name__, "error": str(exc)},
                )

            # Stream ended → emit disconnect, then poll for the client again.
            if was_connected and not self._closed:
                yield {"type": "disconnected"}
                was_connected = False
            if not await self._sleep_unless_closed(self.poll_interval):
                return

    async def _stream_sessions(
        self, lockfile: LockfileInfo, lockfile_path: Path
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield ``{"type": "session", "data": ...}`` from REST + WS until
        the client closes (lockfile vanishes, WS Delete event, or close())."""
        logger.info(
            "lcu_stream_open",
            extra={"port": lockfile.port, "protocol": lockfile.protocol},
        )
        async with self._client_factory(lockfile) as client:
            async for event in self._fetch_initial_session(client):
                yield event

            stream = self._stream_factory(lockfile, [CHAMP_SELECT_WS_TOPIC])
            logger.info("lcu_ws_subscribe", extra={"topic": CHAMP_SELECT_WS_TOPIC})
            watcher = asyncio.create_task(self._watch_lockfile(lockfile_path, stream))
            try:
                async with stream:
                    async for raw in stream:
                        if self._closed:
                            return
                        payload = raw.get("payload") or {}
                        evt_type = payload.get("eventType")
                        uri = payload.get("uri")
                        data = payload.get("data")
                        logger.debug(
                            "lcu_ws_event",
                            extra={"event_type": evt_type, "uri": uri,
                                   "has_data": isinstance(data, dict)},
                        )
                        if evt_type in ("Update", "Create") and isinstance(data, dict):
                            logger.info(
                                "lcu_session_yielded",
                                extra={"event_type": evt_type,
                                       "phase": data.get("phase")},
                            )
                            yield {"type": "session", "data": data}
                        elif evt_type == "Delete":
                            # The user dodged / champ select ended / phase
                            # rolled over. The WS connection is fine — only
                            # the *current session* is gone. Soft-signal it
                            # so the orchestrator clears the cached session
                            # without tearing the whole stream down (which
                            # would cause a visible UI flicker before the
                            # next champ select picks up).
                            logger.info("lcu_session_ended_via_delete")
                            yield {"type": "session_ended"}
            finally:
                watcher.cancel()
                try:
                    await watcher
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                logger.info("lcu_stream_closed")

    async def _fetch_initial_session(
        self, client: LcuClient
    ) -> AsyncIterator[dict[str, Any]]:
        """GET the current champ-select session. 404 / errors are swallowed —
        the user may simply not be in champ select right now; WS will pick up
        the next session whenever it starts."""
        logger.info("lcu_initial_get_request", extra={"path": CHAMP_SELECT_REST_PATH})
        try:
            response = await client.get(CHAMP_SELECT_REST_PATH)
        except LcuClientError as exc:
            logger.warning("lcu_initial_get_failed", extra={"error": str(exc)})
            return
        logger.info("lcu_initial_get_response", extra={"status": response.status_code})
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                logger.warning("lcu_initial_get_not_json", extra={"error": str(exc)})
                return
            logger.info(
                "lcu_initial_session_yielded",
                extra={"phase": data.get("phase") if isinstance(data, dict) else None},
            )
            yield {"type": "session", "data": data}

    async def _watch_lockfile(
        self, lockfile_path: Path, stream: LcuEventStream
    ) -> None:
        """Close the WS stream when the client shuts down or we're closing.

        Triggers on either: (a) the lockfile has vanished (client process
        exited), or (b) ``self._closed`` was set externally. Either way the
        running stream needs to exit so the parent generator can return.
        """
        while True:
            await asyncio.sleep(self.poll_interval)
            if self._closed or not lockfile_path.is_file():
                await stream.close()
                return

    async def _sleep_unless_closed(self, seconds: float) -> bool:
        try:
            await asyncio.sleep(max(seconds, 0))
        except asyncio.CancelledError:
            return False
        return not self._closed
