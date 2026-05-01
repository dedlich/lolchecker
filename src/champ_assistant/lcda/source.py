"""Polling source that emits LCDA snapshots as long as a game is running.

Lifecycle:
  - LCDA only exists while the user is loaded into a match.
  - We poll ``/allgamedata`` every ``poll_interval`` seconds.
  - When the endpoint goes silent for ``stale_after`` seconds, we treat the
    game as ended and the source goes idle (callback receives ``None``).
  - When LCDA comes back, we resume.

The source is a long-lived asyncio task; the orchestrator owns it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .client import LcdaClient, LcdaUnavailable
from .objectives import ObjectiveTimer, compute_objectives
from .players import (
    LivePlayer,
    TeamAggregate,
    aggregate_team,
    enemies_of,
    parse_players,
)
from .power_spikes import PowerSpike, detect_spikes, extract_active_state

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_STALE_AFTER = 6.0
# When LCDA has been unreachable for a while (the user isn't in a game),
# slow down the poll loop to one ping every 30s so we're not pegging
# 127.0.0.1:2999 with a connect-timeout-storm.
OFFLINE_BACKOFF_INTERVAL = 30.0


@dataclass(frozen=True)
class LcdaSnapshot:
    """One tick from the live game."""

    game_time: float
    game_mode: str
    objectives: list[ObjectiveTimer]
    enemies: list[LivePlayer]
    active_summoner: str
    raw_events: list[dict]
    active_level: int = 0
    active_items: int = 0
    new_spikes: list[PowerSpike] = field(default_factory=list)
    allies: list[LivePlayer] = field(default_factory=list)
    active_team: str = ""
    enemy_team: str = ""
    ally_aggregate: TeamAggregate | None = None
    enemy_aggregate: TeamAggregate | None = None
    # "Win", "Lose", or "" when game is still in progress.
    # Populated from the GameEnd event in raw_events.
    game_result: str = ""


SnapshotCallback = Callable[[LcdaSnapshot | None], Awaitable[None] | None]


class LcdaSource:
    """Poll LCDA and notify a callback with each snapshot.

    The callback receives ``None`` when LCDA transitions from reachable to
    unreachable — UI uses that to hide its in-game widgets.
    """

    def __init__(
        self,
        client: LcdaClient,
        on_snapshot: SnapshotCallback,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        stale_after: float = DEFAULT_STALE_AFTER,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._on_snapshot = on_snapshot
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._clock = clock
        self._closed = False
        self._was_alive = False
        self._last_seen: float | None = None
        self._prev_level = 0
        self._prev_items = 0

    async def run(self) -> None:
        """Loop until ``close()`` is called.

        Uses two cadences: the configured ``poll_interval`` while LCDA is
        reachable, and ``OFFLINE_BACKOFF_INTERVAL`` (30s) once we've given
        up after a stale window. This keeps localhost:2999 from drowning
        in connect-timeout retries during champ select.
        """
        while not self._closed:
            await self._tick()
            interval = (
                self._poll_interval if self._was_alive else OFFLINE_BACKOFF_INTERVAL
            )
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        try:
            data = await self._client.all_game_data()
        except LcdaUnavailable:
            await self._handle_unreachable()
            return
        except Exception as exc:
            logger.warning("lcda_tick_failed: %s", exc)
            await self._handle_unreachable()
            return

        snapshot = self._snapshot_from(data)
        self._was_alive = True
        self._last_seen = self._clock()
        await _maybe_await(self._on_snapshot(snapshot))

    async def _handle_unreachable(self) -> None:
        if not self._was_alive:
            return
        now = self._clock()
        if self._last_seen is None or (now - self._last_seen) >= self._stale_after:
            logger.info("lcda_session_ended")
            self._was_alive = False
            self._last_seen = None
            await _maybe_await(self._on_snapshot(None))

    def _snapshot_from(self, data: dict) -> LcdaSnapshot:
        game = data.get("gameData") or {}
        events_block = data.get("events") or {}
        events = events_block.get("Events") or []
        game_time = float(game.get("gameTime") or 0.0)
        all_players = parse_players(list(data.get("allPlayers") or []))
        active = data.get("activePlayer") or {}
        active_name = str(active.get("summonerName") or "")
        active_team = self._team_of(all_players, active_name)

        new_level, new_items = extract_active_state(active)
        spikes = detect_spikes(
            prev_level=self._prev_level,
            new_level=new_level,
            prev_items=self._prev_items,
            new_items=new_items,
        )
        self._prev_level = new_level
        self._prev_items = new_items

        from .players import allies_of
        allies = allies_of(all_players, active_team) if active_team else []
        enemy_team = next(
            (t for t in {p.team for p in all_players} if t and t != active_team),
            "",
        )
        ally_agg = (
            aggregate_team(all_players, list(events), active_team)
            if active_team else None
        )
        enemy_agg = (
            aggregate_team(all_players, list(events), enemy_team)
            if enemy_team else None
        )
        game_result = _extract_game_result(events)
        return LcdaSnapshot(
            game_time=game_time,
            game_mode=str(game.get("gameMode") or ""),
            objectives=compute_objectives(list(events), game_time),
            enemies=enemies_of(all_players, active_team),
            active_summoner=active_name,
            raw_events=list(events),
            active_level=new_level,
            active_items=new_items,
            new_spikes=spikes,
            allies=allies,
            active_team=active_team,
            enemy_team=enemy_team,
            ally_aggregate=ally_agg,
            enemy_aggregate=enemy_agg,
            game_result=game_result,
        )

    @staticmethod
    def _team_of(players: list[LivePlayer], summoner_name: str) -> str:
        for p in players:
            if p.summoner_name == summoner_name:
                return p.team
        return ""

    def close(self) -> None:
        self._closed = True


def _extract_game_result(events: list[dict]) -> str:
    """Return "Win", "Lose", or "" from the first GameEnd event found."""
    for evt in reversed(events):
        if evt.get("EventName") == "GameEnd":
            return str(evt.get("Result") or "")
    return ""


async def _maybe_await(value: object) -> None:
    if asyncio.iscoroutine(value):
        await value
