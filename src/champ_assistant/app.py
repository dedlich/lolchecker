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
from pathlib import Path

from .advisor.composition import CompositionGap, analyze_composition
from .advisor.counters import find_counters
from .advisor.picks import PickSuggestion, suggest_picks
from .data.models import (
    BuildLibrary,
    ChampionBuild,
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
# enemy team's roles from their index within their_team as a last resort.
_DRAFT_ROLE_ORDER: list[Role] = ["TOP", "JUNGLE", "MID", "BOT", "SUPPORT"]


def _role_at_index(i: int) -> Role | None:
    return _DRAFT_ROLE_ORDER[i] if 0 <= i < len(_DRAFT_ROLE_ORDER) else None


def infer_role_from_tags(tags: list[str]) -> Role | None:
    """Heuristic role guess from Data Dragon's champion tags.

    Riot's tags ({Assassin, Fighter, Mage, Marksman, Support, Tank}) are
    playstyle labels, not lanes — so the mapping is approximate. Hand-curated
    priority order based on common pick distribution; user can override.
    """
    s = set(tags)
    if "Marksman" in s:
        return "BOT"
    if "Support" in s:
        return "SUPPORT"
    # Pure tank without fighter chops → typically SUPPORT (Leona, Naut, Alistar)
    if "Tank" in s and "Fighter" not in s:
        return "SUPPORT"
    # Tank + Fighter → top-lane bruisers (Garen, Maokai, Sett)
    if "Tank" in s and "Fighter" in s:
        return "TOP"
    # Pure mage → mid (Annie, Lux without support, Veigar)
    if "Mage" in s and "Assassin" not in s and "Fighter" not in s:
        return "MID"
    # Assassin + Fighter → jungle (Kha'Zix, Viego, Nidalee)
    if "Assassin" in s and "Fighter" in s:
        return "JUNGLE"
    # Pure assassin → mid (Zed, Talon, Akali)
    if "Assassin" in s:
        return "MID"
    # Pure fighter → top (Darius, Aatrox, Camille)
    if "Fighter" in s:
        return "TOP"
    # Mage + Assassin (LeBlanc, Diana) → mid
    if "Mage" in s:
        return "MID"
    return None
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
        builds: BuildLibrary | None = None,
        view_callback: Callable[[SessionView], None] | None = None,
    ) -> None:
        self.source = source
        self.overlay = overlay
        self.counters = counters
        self.tiers = tiers
        self.tags = tags
        self.champions = champions
        self.builds = builds or BuildLibrary()
        # Allow tests to observe view updates without going through Qt.
        self._view_callback = view_callback

        self._latest_session: ChampSelectSession | None = None
        self._connection_state: ConnectionState = "disconnected"
        # Per-cell manual role overrides for the enemy team. cell_id → Role.
        # Cleared when a session is reset (disconnect / different game).
        self._enemy_role_overrides: dict[int, Role] = {}

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
                # Pydantic's ValidationError has a useful __str__ — put it in
                # the message itself, not just extra= which vanishes through
                # our default formatter.
                logger.warning("session_parse_failed: %s", exc)
                self._dump_failed_payload(data)
                return self._push_view(SessionView(connection_state=self._connection_state))
            self._connection_state = "connected"
            view = self._build_view(self._latest_session)
        else:
            # Unknown event — don't crash, just ignore.
            logger.debug("ignored_event", extra={"detail": str(event_type)})
            return self._push_view(SessionView(connection_state=self._connection_state))

        return self._push_view(view)

    @staticmethod
    def _dump_failed_payload(data: object) -> None:
        """Write the raw payload that Pydantic rejected to a JSON file next
        to the rotating log handler so we have a real-world fixture to fix
        the model with."""
        try:
            import json as _json
            from datetime import datetime
            log_dir: Path | None = None
            for h in logging.getLogger().handlers:
                base = getattr(h, "baseFilename", None)
                if base:
                    log_dir = Path(base).parent
                    break
            if log_dir is None:
                return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            dump = log_dir / f"failed_payload_{stamp}.json"
            dump.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
            logger.warning("session_parse_failed_payload_dumped: %s", dump)
        except Exception:  # noqa: BLE001
            logger.exception("payload_dump_failed")

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
        enemy_keys = self._compute_enemy_keys(session)
        enemy_roles = self._compute_enemy_roles(session)
        suggestions, gaps = self._compute_picks(session)

        # Look up the recommended build for each suggestion in the local
        # player's role. Falls back to {} silently when builds aren't seeded.
        my_role: Role | None = None
        if session.me is not None:
            my_role = session.me.assigned_position
        suggestion_builds: dict[str, ChampionBuild] = {}
        if my_role is not None:
            for s in suggestions:
                build = self.builds.build_for(s.champion_key, my_role)
                if build is not None:
                    suggestion_builds[s.champion_key] = build

        return SessionView(
            connection_state=self._connection_state,
            session=session,
            enemy_counters=enemy_counters,
            suggestions=suggestions,
            gaps=gaps,
            enemy_names=enemy_names,
            enemy_keys=enemy_keys,
            enemy_roles=enemy_roles,
            enemy_role_overridden=set(self._enemy_role_overrides.keys()),
            suggestion_builds=suggestion_builds,
        )

    def _resolve_enemy_role(
        self, enemy: TeamMember, index: int, champion: Champion | None
    ) -> Role | None:
        """Resolve an enemy slot's role with this priority:
        1. Manual override (user clicked the role label in the UI)
        2. assigned_position from the LCU (rare — usually empty for the enemy)
        3. Tag-based heuristic from the picked champion's Data Dragon tags
        4. Cell-order fallback (TOP/JUNGLE/MID/BOT/SUPPORT by index)
        """
        override = self._enemy_role_overrides.get(enemy.cell_id)
        if override is not None:
            return override
        if enemy.assigned_position is not None:
            return enemy.assigned_position
        if champion is not None:
            inferred = infer_role_from_tags(champion.tags)
            if inferred is not None:
                return inferred
        return _role_at_index(index)

    def _compute_enemy_counters(
        self, session: ChampSelectSession
    ) -> dict[int, list]:
        result: dict[int, list] = {}
        for i, enemy in enumerate(session.their_team):
            if enemy.champion_id == 0:
                continue
            champ = self.champions.get(enemy.champion_id)
            role = self._resolve_enemy_role(enemy, i, champ)
            if role is None or champ is None:
                continue
            result[enemy.cell_id] = find_counters(
                champ.key, role, self.counters, limit=3
            )
        return result

    def _compute_enemy_roles(
        self, session: ChampSelectSession
    ) -> dict[int, Role]:
        """Resolved role per enemy cell — surfaces to the UI for the role label."""
        result: dict[int, Role] = {}
        for i, enemy in enumerate(session.their_team):
            champ = (
                self.champions.get(enemy.champion_id)
                if enemy.champion_id else None
            )
            role = self._resolve_enemy_role(enemy, i, champ)
            if role is not None:
                result[enemy.cell_id] = role
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

    def _compute_enemy_keys(self, session: ChampSelectSession) -> dict[int, str]:
        keys: dict[int, str] = {}
        for enemy in session.their_team:
            if enemy.champion_id == 0:
                continue
            champ = self.champions.get(enemy.champion_id)
            if champ is not None:
                keys[enemy.champion_id] = champ.key
        return keys

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

    # -- Live data updates ------------------------------------------------

    def cycle_enemy_role_override(self, cell_id: int) -> Role | None:
        """Advance the manual override for an enemy cell through the cycle:
        none → TOP → JUNGLE → MID → BOT → SUPPORT → none.

        Returns the new override value (or None when cleared). Triggers a
        re-render of the latest cached view so counters/suggestions update
        instantly.
        """
        cycle: list[Role | None] = [
            None, "TOP", "JUNGLE", "MID", "BOT", "SUPPORT",
        ]
        current = self._enemy_role_overrides.get(cell_id)
        try:
            next_index = (cycle.index(current) + 1) % len(cycle)
        except ValueError:
            next_index = 1  # current wasn't in cycle, restart at TOP
        next_role = cycle[next_index]
        if next_role is None:
            self._enemy_role_overrides.pop(cell_id, None)
        else:
            self._enemy_role_overrides[cell_id] = next_role
        if self._latest_session is not None:
            view = self._build_view(self._latest_session)
            self._push_view(view)
        return next_role

    def set_enemy_role_override(self, cell_id: int, role: Role | None) -> None:
        """Direct setter for tests / programmatic control."""
        if role is None:
            self._enemy_role_overrides.pop(cell_id, None)
        else:
            self._enemy_role_overrides[cell_id] = role
        if self._latest_session is not None:
            view = self._build_view(self._latest_session)
            self._push_view(view)

    def update_champions(self, champions: dict[int, Champion]) -> None:
        """Replace the champion lookup table and re-render the latest view.

        Called once at startup after Data Dragon returns the full ~170-champ
        list. Without this we'd be limited to the hardcoded ~30-champ
        bootstrap dict and the UI would show "Champion #89" for anything
        outside that subset.
        """
        if not champions:
            return
        self.champions = champions
        if self._latest_session is not None:
            view = self._build_view(self._latest_session)
            self._push_view(view)

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
