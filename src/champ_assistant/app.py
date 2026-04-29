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

import asyncio
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
    CounterEntry,
    CounterMatrix,
    Role,
    TagsData,
    TeamMember,
    TierList,
)
from .data.runtime_counters import RuntimeCounterStore

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
        runtime_counters: RuntimeCounterStore | None = None,
        profile_service: object | None = None,
        view_callback: Callable[[SessionView], None] | None = None,
    ) -> None:
        self.source = source
        self.overlay = overlay
        self.counters = counters
        self.tiers = tiers
        self.tags = tags
        self.champions = champions
        self.builds = builds or BuildLibrary()
        self._runtime_counters = runtime_counters
        self._profile_service = profile_service
        # Track in-flight runtime fetches to avoid duplicate scheduling.
        self._runtime_inflight: set[tuple[str, str]] = set()
        self._profile_inflight: set[str] = set()
        self._enemy_profiles_by_cell: dict[int, object] = {}
        # Mirror for the player's own team (used by the lobby panel
        # during loading screen / post-finalization). The local player
        # is skipped — fetching your own profile is just a wasted API
        # call against the rate limit.
        self._ally_profiles_by_cell: dict[int, object] = {}
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
            logger.info("orchestrator_state state=waiting")
            self._connection_state = "waiting"
            view = SessionView(connection_state="waiting", session=self._latest_session)
        elif event_type == "disconnected":
            logger.info("orchestrator_state state=disconnected")
            self._connection_state = "disconnected"
            view = SessionView(connection_state="disconnected")
            self._latest_session = None
        elif event_type == "session_ended":
            # LCU emitted a Delete WS event for the champ-select session —
            # current draft is over (dodge / phase rolled / lobby left) but
            # the client is still alive. Clear our cached session but stay
            # connected so the next session picks up without a UI flicker.
            logger.info("session_ended_received clearing cached session")
            self._latest_session = None
            self._enemy_role_overrides.clear()
            self._enemy_profiles_by_cell.clear()
            self._ally_profiles_by_cell.clear()
            if self._profile_service is not None:
                self._profile_service.clear()
            view = SessionView(connection_state=self._connection_state)
        elif event_type == "connected":
            logger.info("orchestrator_state state=connected")
            self._connection_state = "connected"
            view = self._build_view(self._latest_session)
        elif event_type == "session":
            data = event.get("data")
            if not isinstance(data, dict):
                logger.warning("session_event_missing_data type=%r", event_type)
                return self._push_view(SessionView(connection_state=self._connection_state))
            try:
                session = ChampSelectSession.model_validate(data)
            except Exception as exc:
                logger.warning("session_parse_failed: %s", exc)
                self._dump_failed_payload(data)
                return self._push_view(SessionView(connection_state=self._connection_state))
            self._latest_session = session
            self._connection_state = "connected"
            logger.info(
                "session_received phase=%r my_team=%d their_team=%d local_cell=%d",
                session.phase,
                len(session.my_team),
                len(session.their_team),
                session.local_player_cell_id,
            )
            view = self._build_view(session)
        else:
            # Unknown event — don't crash, just ignore.
            logger.debug("ignored_event type=%r", event_type)
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
        # Builds are matchup-adapted: take the base role build and
        # apply heuristic swaps based on enemy team comp (boots vs
        # AP/AD-heavy, anti-heal vs sustain, tenacity vs heavy CC).
        suggestion_builds: dict[str, ChampionBuild] = {}
        suggestion_build_reasons: dict[str, list[str]] = {}
        if my_role is not None:
            from .advisor.build_adapter import adapt_build
            enemy_keys_list = list(self._team_keys(session.their_team))
            for s in suggestions:
                base = self.builds.build_for(s.champion_key, my_role)
                adapted = adapt_build(
                    base, role=my_role,
                    enemy_team_keys=enemy_keys_list,
                    tags=self.tags,
                )
                if adapted is None:
                    continue
                suggestion_builds[s.champion_key] = adapted.build
                if adapted.reasons:
                    suggestion_build_reasons[s.champion_key] = adapted.reasons

        # Kick off async profile fetches for enemies whose puuid/summoner_id
        # is now visible. Results land in self._enemy_profiles_by_cell and
        # the next view rebuild will surface them.
        self._maybe_fetch_profiles(session)

        from .advisor.ban_suggestions import suggest_bans
        bans = suggest_bans(
            session=session,
            champions=self.champions,
            tiers=self.tiers,
            enemy_profiles=self._enemy_profiles_by_cell,  # type: ignore[arg-type]
            my_role=my_role,  # lane-aware scoring — tier in MY role gets 1.5×
            limit=3,
        )

        return SessionView(
            connection_state=self._connection_state,
            session=session,
            enemy_counters=enemy_counters,
            suggestions=suggestions,
            gaps=gaps,
            enemy_names=enemy_names,
            enemy_keys=enemy_keys,
            # Global champion maps for EnemyRow's mains-icon row —
            # need to look up arbitrary champions outside the lobby.
            all_champion_keys={
                c.id: c.key for c in self.champions.values()
            },
            all_champion_names={
                c.id: c.name for c in self.champions.values()
            },
            enemy_roles=enemy_roles,
            enemy_role_overridden=set(self._enemy_role_overrides.keys()),
            suggestion_builds=suggestion_builds,
            suggestion_build_reasons=suggestion_build_reasons,
            enemy_profiles=dict(self._enemy_profiles_by_cell),  # type: ignore[arg-type]
            ally_profiles=dict(self._ally_profiles_by_cell),  # type: ignore[arg-type]
            ban_suggestions=bans,
        )

    def _maybe_fetch_profiles(self, session: ChampSelectSession) -> None:
        """If a Riot API key is configured, fire off async profile lookups
        for BOTH teams. Empty/no-key/no-puuid → noop. Local player is
        skipped (wasted API call). Cached results show up on the next
        view rebuild without blocking session rendering.

        Rate-limit reality: 10 players × ~4 API endpoints = ~40 calls
        per champ-select. Personal Riot dev keys allow 100 / 2 min, so
        a single champ-select fits with margin. Repeated lobbies in
        rapid succession may hit the limit — failures degrade
        silently to empty profiles via the existing ProfileService
        error path.
        """
        if self._profile_service is None or not getattr(
            self._profile_service, "enabled", False
        ):
            return
        local_cell = session.local_player_cell_id
        # Schedule fetches for both teams in one pass so the dispatch
        # logic stays in one spot — same try-except, same inflight
        # tracking, same re-render trigger after each completion.
        for member in session.their_team:
            self._schedule_profile_fetch(member, is_ally=False)
        for member in session.my_team:
            if member.cell_id == local_cell:
                continue  # don't fetch our own profile — wasted call
            self._schedule_profile_fetch(member, is_ally=True)

    def _schedule_profile_fetch(
        self,
        member: TeamMember,
        *,
        is_ally: bool,
    ) -> None:
        """Schedule one profile lookup. Idempotent against the
        inflight set + the per-team cache; safe to call repeatedly
        as the session progresses (e.g. summoner-name shows up only
        after lock-in)."""
        cache = (
            self._ally_profiles_by_cell if is_ally
            else self._enemy_profiles_by_cell
        )
        if member.cell_id < 0 or member.cell_id in cache:
            return
        if not member.puuid and not member.summoner_id:
            return
        key = member.puuid or f"sid:{member.summoner_id}"
        # Inflight key is team-prefixed so the same puuid being
        # fetched for ally + enemy doesn't collide (rare but possible
        # in test fixtures).
        inflight_key = f"{'a' if is_ally else 'e'}:{key}"
        if inflight_key in self._profile_inflight:
            return
        self._profile_inflight.add(inflight_key)
        try:
            import asyncio as _aio
            _aio.create_task(
                self._fetch_one_profile(member, inflight_key, is_ally=is_ally)
            )
        except RuntimeError:
            # No running loop (tests or sync use) — skip.
            self._profile_inflight.discard(inflight_key)

    async def _fetch_one_profile(
        self,
        member: TeamMember,
        inflight_key: str,
        *,
        is_ally: bool = False,
    ) -> None:
        try:
            assert self._profile_service is not None
            if member.puuid:
                profile = await self._profile_service.fetch_by_puuid(member.puuid)
            else:
                profile = await self._profile_service.fetch_by_summoner_id(
                    member.summoner_id
                )
            cache = (
                self._ally_profiles_by_cell if is_ally
                else self._enemy_profiles_by_cell
            )
            cache[member.cell_id] = profile
            # Re-render so the freshly fetched profile shows up in the UI.
            if self._latest_session is not None:
                self._push_view(self._build_view(self._latest_session))
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "profile_fetch_failed cell=%d ally=%s: %s",
                member.cell_id, is_ally, exc,
            )
        finally:
            self._profile_inflight.discard(inflight_key)

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

    def _lookup_counters(
        self, enemy_key: str, role: Role
    ) -> list[CounterEntry]:
        """Three-tier counter resolution:
          1. Seed JSON (deterministic, instant)
          2. Runtime cache (Groq response we already fetched)
          3. Fire-and-forget Groq fetch — view will re-render when it lands
        """
        seed = find_counters(enemy_key, role, self.counters, limit=5)
        if seed:
            return seed
        if self._runtime_counters is not None:
            cached = self._runtime_counters.get_cached(enemy_key, role)
            if cached:
                return cached
            self._schedule_runtime_fetch(enemy_key, role)
        return []

    def _schedule_runtime_fetch(self, enemy_key: str, role: Role) -> None:
        """Kick off a Groq fetch in the background; re-renders on success.

        Idempotent — returns early if a fetch for this matchup is already
        in flight or if the runtime store isn't enabled (no API key).
        """
        store = self._runtime_counters
        if store is None or not store.enabled:
            return
        key = (enemy_key, role)
        if key in self._runtime_inflight:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Not inside an event loop (e.g. unit tests). Skip — caller
            # can still await store.get() directly if they need the data.
            return
        self._runtime_inflight.add(key)

        async def _fetch_and_rerender() -> None:
            try:
                counters = await store.get(enemy_key, role)
                if counters and self._latest_session is not None:
                    view = self._build_view(self._latest_session)
                    self._push_view(view)
            except Exception:  # noqa: BLE001
                logger.exception("runtime_fetch_failed")
            finally:
                self._runtime_inflight.discard(key)

        loop.create_task(_fetch_and_rerender(), name=f"runtime-fetch-{enemy_key}")

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
            counters = self._lookup_counters(champ.key, role)
            result[enemy.cell_id] = counters[:3]
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

        my_role = me.assigned_position
        my_keys = self._team_keys(session.my_team)
        enemy_keys = self._team_keys(session.their_team)
        gaps = analyze_composition(my_keys, self.tags)

        # If the lane opponent is locked in, prioritize counters specifically
        # against them — but still keep team-comp synergy in the score so
        # we don't recommend a counter pick that breaks the team's needs.
        lane_opponent = self._enemy_in_role(session, my_role)
        if lane_opponent is not None:
            counters = self._lookup_counters(lane_opponent.key, my_role)
            if counters:
                drafted = {k for k in (my_keys + enemy_keys) if k}
                lane_suggestions = self._suggestions_from_counters(
                    counters,
                    lane_opponent_key=lane_opponent.key,
                    drafted=drafted,
                    my_role=my_role,
                    gaps=gaps,
                )
                if lane_suggestions:
                    return lane_suggestions[:5], gaps

        # Fallback: tier-based suggestions when no lane opponent yet OR
        # we have no counter data for them.
        suggestions = suggest_picks(
            my_role,
            my_keys,
            enemy_keys,
            gaps,
            self.tiers,
            self.counters,
            self.tags,
            limit=5,
        )
        return suggestions, gaps

    def _enemy_in_role(
        self, session: ChampSelectSession, target_role: Role
    ) -> Champion | None:
        for i, enemy in enumerate(session.their_team):
            if enemy.champion_id == 0:
                continue
            champ = self.champions.get(enemy.champion_id)
            if champ is None:
                continue
            role = self._resolve_enemy_role(enemy, i, champ)
            if role == target_role:
                return champ
        return None

    def _suggestions_from_counters(
        self,
        counters: list[CounterEntry],
        *,
        lane_opponent_key: str,
        drafted: set[str],
        my_role: Role,
        gaps: list[CompositionGap],
    ) -> list[PickSuggestion]:
        """Convert raw counter list into scored PickSuggestions.

        Score combines:
          - counter strength (CounterEntry.score × 8, range ~0-80) — primary
          - tier bonus from tiers.json if present (S+: 10, S: 7, A: 4, ...) — secondary
          - composition gap-fill (matches advisor.picks._GAP_FILL_BONUS) — tertiary
        Drafted champions excluded; result clamped to [0, 100].
        """
        from .advisor.picks import _GAP_FILL_BONUS, _GAP_TAGS, _TIER_SCORE

        out: list[PickSuggestion] = []
        for c in counters:
            if c.champion in drafted:
                continue
            counter_score = min(c.score * 8.0, 80.0)
            tier_score = _TIER_SCORE.get(c.tier or "", 0.0) * 0.5  # halved
            champ_tags = set(self.tags.tags_for(c.champion))
            gap_score = 0.0
            gap_reasons: list[str] = []
            for gap in gaps:
                tags_for_gap = _GAP_TAGS.get(gap.category, set())
                if champ_tags & tags_for_gap:
                    bonus = _GAP_FILL_BONUS.get(gap.severity, 0.0)
                    gap_score += bonus
                    gap_reasons.append(f"fills {gap.category}")

            total = max(0.0, min(100.0, counter_score + tier_score + gap_score))
            reasons = [f"Counters {lane_opponent_key} ({c.score:.1f})"]
            if c.tier:
                reasons.append(f"{c.tier} tier")
            reasons.extend(gap_reasons[:2])

            out.append(
                PickSuggestion(
                    champion_key=c.champion,
                    score=total,
                    tier=c.tier,
                    reasons=reasons,
                )
            )
        # Already sorted by counter strength in seed/Groq, but stabilize anyway.
        out.sort(key=lambda s: -s.score)
        return out

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
