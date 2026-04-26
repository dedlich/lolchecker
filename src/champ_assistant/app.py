"""Application orchestrator.

Wires the four layers built in Phases 2-5:

    LcuSource events  → ChampSelectSession (parsed via Pydantic model with
                                            LCU camelCase aliases)
                      → SessionView (advisor lookups for counters,
                                     suggestions, gaps; champion name resolution)
                      → MainOverlay.update_view  (Qt UI refresh)

Pure logic up front. All Qt/asyncio glue is at the edges (run() and
__main__.py) so the orchestration is unit-testable without an event loop.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from .advisor.composition import CompositionGap, analyze_composition
from .advisor.counters import find_counters
from .advisor.picks import PickSuggestion, suggest_picks
from .data.models import (
    ChampSelectSession,
    Champion,
    CounterMatrix,
    Role,
    TagsData,
    TeamMember,
    TierList,
)

# Standard draft pick order — Riot fixes the role assignment to cell order in
# ranked, but the LCU only echoes assignedPosition for *my* team. We infer the
# enemy team's roles from their index within their_team.
_DRAFT_ROLE_ORDER: list[Role] = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]


def _role_at_index(i: int) -> Role | None:
    return _DRAFT_ROLE_ORDER[i] if 0 <= i < len(_DRAFT_ROLE_ORDER) else None
from .lcu.sources import LcuSource
from .ui.overlay import MainOverlay
from .ui.view_model import ConnectionState, SessionView

logger = logging.getLogger(__name__)


class ChampAssistant:
    """Orchestrates source events into UI updates.

    Pure-Python except for the ``MainOverlay`` reference. ``handle_event``
    is a sync method: feed it a dict from the source and it computes +
    pushes a ``SessionView`` to the overlay. This makes integration tests
    trivial — no asyncio / qasync required.
    """

    def __init__(
        self,
        *,
        source: LcuSource,
        overlay: MainOverlay,
        counters: CounterMatrix,
        tiers: TierList,
        tags: TagsData,
        champions: dict[int, Champion],
        view_callback: Callable[[SessionView], None] | None = None,
    ) -> None:
        self.source = source
        self.overlay = overlay
        self.counters = counters
        self.tiers = tiers
        self.tags = tags
        self.champions = champions
        # Allow tests to observe view updates without going through Qt.
        self._view_callback = view_callback

        self._latest_session: ChampSelectSession | None = None
        self._connection_state: ConnectionState = "disconnected"

        # Wire UI: refresh shortcut re-renders the latest session view.
        self.overlay.refresh_requested.connect(self._on_refresh_requested)

    # -- Event ingestion --------------------------------------------------

    def handle_event(self, event: dict[str, object]) -> SessionView:
        """Process one source event and push a SessionView to the overlay.

        Returns the SessionView that was sent — useful for tests to assert
        without inspecting the overlay's internal state.
        """
        event_type = event.get("type")

        if event_type == "waiting_for_client":
            self._connection_state = "waiting"
            view = SessionView(connection_state="waiting", session=self._latest_session)
        elif event_type == "disconnected":
            self._connection_state = "disconnected"
            view = SessionView(connection_state="disconnected")
            self._latest_session = None
        elif event_type == "connected":
            self._connection_state = "connected"
            view = self._build_view(self._latest_session)
        elif event_type == "session":
            data = event.get("data")
            if not isinstance(data, dict):
                logger.warning("session_event_missing_data", extra={"detail": str(event)})
                return self._push_view(SessionView(connection_state=self._connection_state))
            try:
                self._latest_session = ChampSelectSession.model_validate(data)
            except Exception as exc:
                logger.warning("session_parse_failed", extra={"error": repr(exc)})
                return self._push_view(SessionView(connection_state=self._connection_state))
            self._connection_state = "connected"
            view = self._build_view(self._latest_session)
        else:
            # Unknown event — don't crash, just ignore.
            logger.debug("ignored_event", extra={"detail": str(event_type)})
            return self._push_view(SessionView(connection_state=self._connection_state))

        return self._push_view(view)

    def _push_view(self, view: SessionView) -> SessionView:
        self.overlay.update_view(view)
        if self._view_callback is not None:
            self._view_callback(view)
        return view

    # -- View construction -----------------------------------------------

    def _build_view(self, session: ChampSelectSession | None) -> SessionView:
        if session is None:
            return SessionView(connection_state=self._connection_state)

        enemy_counters = self._compute_enemy_counters(session)
        enemy_names = self._compute_enemy_names(session)
        suggestions, gaps = self._compute_picks(session)

        return SessionView(
            connection_state=self._connection_state,
            session=session,
            enemy_counters=enemy_counters,
            suggestions=suggestions,
            gaps=gaps,
            enemy_names=enemy_names,
        )

    def _compute_enemy_counters(
        self, session: ChampSelectSession
    ) -> dict[int, list]:
        result: dict[int, list] = {}
        for i, enemy in enumerate(session.their_team):
            if enemy.champion_id == 0:
                continue
            role = enemy.assigned_position or _role_at_index(i)
            if role is None:
                continue
            champ = self.champions.get(enemy.champion_id)
            if champ is None:
                continue
            result[enemy.cell_id] = find_counters(
                champ.key, role, self.counters, limit=3
            )
        return result

    def _compute_enemy_names(self, session: ChampSelectSession) -> dict[int, str]:
        names: dict[int, str] = {}
        for enemy in session.their_team:
            if enemy.champion_id == 0:
                continue
            champ = self.champions.get(enemy.champion_id)
            if champ is not None:
                names[enemy.champion_id] = champ.name
        return names

    def _compute_picks(
        self, session: ChampSelectSession
    ) -> tuple[list[PickSuggestion], list[CompositionGap]]:
        me = session.me
        if me is None or me.assigned_position is None:
            return [], []

        my_keys = self._team_keys(session.my_team)
        enemy_keys = self._team_keys(session.their_team)
        gaps = analyze_composition(my_keys, self.tags)
        suggestions = suggest_picks(
            me.assigned_position,
            my_keys,
            enemy_keys,
            gaps,
            self.tiers,
            self.counters,
            self.tags,
            limit=5,
        )
        return suggestions, gaps

    def _team_keys(self, team: list[TeamMember]) -> list[str]:
        keys: list[str] = []
        for member in team:
            if member.champion_id == 0:
                continue
            champ = self.champions.get(member.champion_id)
            if champ is not None:
                keys.append(champ.key)
        return keys

    # -- Refresh hook (UI Ctrl+R) -----------------------------------------

    def _on_refresh_requested(self) -> None:
        view = self._build_view(self._latest_session)
        self._push_view(view)

    # -- Async runner -----------------------------------------------------

    async def run(self) -> None:
        """Consume events from the source until close() / cancellation."""
        async for event in self.source.events():
            try:
                self.handle_event(event)
            except Exception:
                # Per masterplan §4.1: swallow + log, never let the loop die.
                logger.exception("orchestrator_handler_failed")
