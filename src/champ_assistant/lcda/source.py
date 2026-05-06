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
    allies_of,
    enemies_of,
    parse_players,
)
from .active_state import ActiveCombatState, extract_active_combat_state
from .gank_window import GankAlert, detect_gank_risk
from .lane_state import LaneOpponentMia, detect_lane_opponent_mia
from .power_spikes import EnemySpike, PowerSpike, detect_enemy_spikes, detect_spikes, extract_active_state
from .tilt import TiltState, detect_tilt

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
    # Enemy item-completion spikes this tick — non-empty only on the tick
    # where an enemy crossed a legendary-item threshold (1st / 2nd / 3rd).
    enemy_spikes: list[EnemySpike] = field(default_factory=list)
    # Non-None on ticks where the enemy jungler has been unaccounted-for
    # long enough to warrant a gank warning (Charter B2).
    gank_alert: GankAlert | None = None
    # Active player's tilt state — death-pattern coaching (Charter B4).
    # ``None`` only when the active player hasn't died yet this game.
    tilt_state: TiltState | None = None
    # Active player's HP %, mana %, current gold — drives recall + low-HP
    # rules. Always populated (defaults to all-full when activePlayer is
    # missing) so rules can read fields without None-checks.
    active_combat: ActiveCombatState = field(default_factory=ActiveCombatState)
    # Active player's lane opponent absent from CS — companion to gank_alert.
    # None when nobody is missing, no opponent identified, or active player
    # is JUNGLE / UTILITY (signal degrades for those positions).
    lane_opponent_alert: LaneOpponentMia | None = None


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
        self._prev_enemy_counts: dict[str, int] = {}  # champion_name → legendary count
        self._jungler_last_seen_gt: float = 0.0       # gank-window tracking
        self._jungler_was_alive: bool = False
        # Lane-opponent MIA tracking. Per-enemy CS-delta state across ticks.
        self._enemy_lane_seen: dict[str, float] = {}
        self._enemy_lane_cs: dict[str, int] = {}
        self._enemy_lane_alive: dict[str, bool] = {}

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
        active_combat = extract_active_combat_state(active)
        spikes = detect_spikes(
            prev_level=self._prev_level,
            new_level=new_level,
            prev_items=self._prev_items,
            new_items=new_items,
        )
        self._prev_level = new_level
        self._prev_items = new_items

        # Enemy spike detection — only track the opposing team's champions.
        raw_enemy_players = [
            p for p in (data.get("allPlayers") or [])
            if isinstance(p, dict) and str(p.get("team") or "") != active_team
        ] if active_team else []
        enemy_spikes, new_enemy_counts = detect_enemy_spikes(
            self._prev_enemy_counts, raw_enemy_players,
        )
        self._prev_enemy_counts = new_enemy_counts

        # Gank window detection — infer enemy jungler MIA from kill events.
        enemy_players = enemies_of(all_players, active_team)
        active_player_obj = next(
            (p for p in all_players if p.summoner_name == active_name), None
        )
        active_position = str(getattr(active_player_obj, "position", "") or "")
        gank_alert, new_jlsg, new_jwa = detect_gank_risk(
            active_position=active_position,
            enemies=enemy_players,
            events=list(events),
            game_time=game_time,
            prev_last_seen_gt=self._jungler_last_seen_gt,
            prev_was_alive=self._jungler_was_alive,
        )
        self._jungler_last_seen_gt = new_jlsg
        self._jungler_was_alive = new_jwa

        # Lane-opponent MIA — uses LivePlayer enemy_players (already
        # parsed) so creep_score is accessible without re-parsing.
        lane_alert, new_lane_seen, new_lane_cs, new_lane_alive = (
            detect_lane_opponent_mia(
                active_position=active_position,
                enemies=enemy_players,
                game_time=game_time,
                prev_last_cs_at=self._enemy_lane_seen,
                prev_cs=self._enemy_lane_cs,
                prev_alive=self._enemy_lane_alive,
            )
        )
        self._enemy_lane_seen = new_lane_seen
        self._enemy_lane_cs = new_lane_cs
        self._enemy_lane_alive = new_lane_alive

        # Tilt detection — track the active player's death pattern.
        # Build the active-player and ally-id sets, matching how kill events
        # identify champions (LCDA uses summoner_name in some versions,
        # champion_name in others — include both to be defensive).
        active_ids: set[str] = set()
        if active_name:
            active_ids.add(active_name)
        if active_player_obj is not None:
            cn = getattr(active_player_obj, "champion_name", "")
            if cn:
                active_ids.add(cn)
        ally_ids: set[str] = set()
        if active_team:
            for p in all_players:
                if p.team != active_team or p.summoner_name == active_name:
                    continue
                if p.summoner_name:
                    ally_ids.add(p.summoner_name)
                if p.champion_name:
                    ally_ids.add(p.champion_name)
        tilt_state = detect_tilt(
            active_ids=active_ids,
            ally_ids=ally_ids,
            events=list(events),
            game_time=game_time,
        )

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
            enemy_spikes=enemy_spikes,
            gank_alert=gank_alert,
            tilt_state=tilt_state,
            active_combat=active_combat,
            lane_opponent_alert=lane_alert,
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
