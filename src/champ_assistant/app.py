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

from pydantic import ValidationError

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
from .advisor.game_plan_llm import GamePlanLLMService
from .data.runtime_counters import RuntimeCounterStore
from .lcu.sources import LcuSource
from .ui.overlay import MainOverlay
from .ui.view_model import ConnectionState, SessionView
# Re-export for callers that historically imported infer_role_from_tags
# from this module (e.g. tests). New code should import from
# ``advisor.role_inference`` directly.
from .advisor.role_inference import infer_role_from_tags  # noqa: F401
from .view_builder import ViewBuilderDeps, build_session_view

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
        game_plan_llm: GamePlanLLMService | None = None,
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
        self._game_plan_llm = game_plan_llm
        self._profile_service = profile_service
        self._game_plan_prefetched_for: str = ""
        # Track in-flight async fan-outs by key to avoid duplicate
        # scheduling. The Coalescer guarantees the key is discarded on
        # task completion (exception or success) so we don't have to
        # sprinkle ``discard()`` calls through every fetch's try/finally.
        from .coalescer import Coalescer
        self._runtime_coalescer: Coalescer[tuple[str, str]] = Coalescer()
        self._profile_coalescer: Coalescer[str] = Coalescer()
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
        # True between LCU's champ-select session-delete and LCDA going
        # live. RosterWindow keys on this so it shows over the loading
        # screen even when the LCU skips the GAME_STARTING phase
        # (which happens often and was the v1.10.121 bug — the dedicated
        # window never appeared because phase == "GAME_STARTING" never
        # fired).
        self._loading_screen_active: bool = False
        # Gameflow-session fallback gate. ``/lol-champ-select/v1/session``
        # privacy-strips puuid + summoner_id from all 9 non-local cells
        # at every phase, so during FINALIZATION / GAME_STARTING we hit
        # ``/lol-gameflow/v1/session`` once to recover the IDs needed for
        # profile lookups. Set on first hit of a session, cleared when a
        # new champ-select arrives so the next game re-runs the fetch.
        self._gameflow_fetch_dispatched: bool = False
        # Last champ-select session we saw before it was deleted —
        # RosterPanel needs the my_team / their_team rosters during the
        # loading screen, but ``_latest_session`` is None once the LCU
        # fires the Delete event. Cached here on session_ended and
        # reused when ``_loading_screen_active`` is True.
        self._loading_screen_session: ChampSelectSession | None = None

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
            # Cache the just-deleted session so the RosterWindow can
            # render the actual roster during the loading screen.
            self._loading_screen_session = self._latest_session
            self._latest_session = None
            self._enemy_role_overrides.clear()
            # Profile caches are NOT cleared here — RosterPanel needs
            # them to render mains / WR / streak during the loading
            # screen. They're dropped when the next session arrives
            # (new game starts) or notify_game_active fires (LCDA live).
            # Gameflow's ``gameData.teamOne/teamTwo`` only refreshes
            # to the current game once gameflow.phase transitions out
            # of ``ChampSelect`` — which happens right around now.
            # Fire the puuid-recovery fetch from here so it sees the
            # new game's roster, not the previous one.
            if not self._gameflow_fetch_dispatched:
                self._gameflow_fetch_dispatched = True
                self._profile_coalescer.schedule(
                    "gameflow_session_fetch",
                    self._fetch_gameflow_session_ids,
                )
            # Champ-select just ended — we're either on the loading screen
            # (game accepted), or the user dodged. Either way, raise the
            # roster window. ``notify_game_active`` (LCDA-driven) and the
            # next ``session`` event clear it. Worst case: a dodge keeps
            # the window up until the next champ-select, which is fine —
            # it's a separate top-level window the user can close.
            self._loading_screen_active = True
            view = self._build_view(self._loading_screen_session)
            view = view.model_copy(update={"loading_screen_active": True})
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
            # Fresh champ-select arrived. Drop any stale profile caches
            # so the new cell_id slots don't render the previous game's
            # players. Two trigger conditions cover every entry into a
            # new draft: dodge → re-queue (loading_screen_active still
            # set), and normal game end → re-queue (latest_session is
            # None because notify_game_active cleared it).
            if self._latest_session is None or self._loading_screen_active:
                self._enemy_profiles_by_cell.clear()
                self._ally_profiles_by_cell.clear()
                if self._profile_service is not None:
                    self._profile_service.clear()
            self._latest_session = session
            self._connection_state = "connected"
            self._loading_screen_active = False
            self._loading_screen_session = None
            self._gameflow_fetch_dispatched = False
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
            from . import app_paths
            # Single source of truth for the log directory — no longer
            # walks logging handlers (brittle: a JSON handler or rotating
            # file handler swap silently broke the dump path).
            log_dir = app_paths.log_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
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

    def _make_view_deps(self) -> ViewBuilderDeps:
        """Pack the orchestrator's mutable state into the deps struct
        the pure view builder consumes."""
        return ViewBuilderDeps(
            connection_state=self._connection_state,
            counters=self.counters,
            tiers=self.tiers,
            tags=self.tags,
            champions=self.champions,
            builds=self.builds,
            runtime_counters=self._runtime_counters,
            enemy_role_overrides=self._enemy_role_overrides,
            enemy_profiles_by_cell=self._enemy_profiles_by_cell,  # type: ignore[arg-type]
            ally_profiles_by_cell=self._ally_profiles_by_cell,  # type: ignore[arg-type]
            schedule_runtime_fetch=self._schedule_runtime_fetch,
            game_plan_enabled=bool(
                self._game_plan_llm and self._game_plan_llm.enabled
            ),
        )

    def _build_view(self, session: ChampSelectSession | None) -> SessionView:
        """Thin orchestration shim around ``view_builder.build_session_view``.

        The view computation itself is a pure function in ``view_builder``;
        this method also fires the side effect (kick off async profile
        fetches) that has to live with the orchestrator since it touches
        the event loop + Coalescer.
        """
        view = build_session_view(session, self._make_view_deps())
        if session is not None:
            self._maybe_fetch_profiles(session)
            self._maybe_prefetch_game_plan(view)
        # Surface any cached game-plan prose so the right-column reads it.
        if self._game_plan_llm is not None and view.my_champion_key:
            cached = self._game_plan_llm.get_cached(
                champion=view.my_champion_key,
                role=str(view.my_champion_role or ""),
                allies=self._team_keys_from_session(session, ally=True),
                enemies=self._team_keys_from_session(session, ally=False),
            )
            if cached:
                view = view.model_copy(update={"game_plan_text": cached})
        if self._loading_screen_active:
            view = view.model_copy(update={"loading_screen_active": True})
        return view

    def _team_keys_from_session(
        self,
        session: "ChampSelectSession | None",
        *,
        ally: bool,
    ) -> list[str]:
        if session is None:
            return []
        members = session.my_team if ally else session.their_team
        keys: list[str] = []
        for m in members:
            if not m.champion_id:
                continue
            champ = self.champions.get(m.champion_id)
            if champ is not None:
                keys.append(champ.key)
        return keys

    def _maybe_prefetch_game_plan(self, view: SessionView) -> None:
        """Fire-and-forget LLM prefetch when the local champion locks in.

        Triggers ONCE per (champ, role, enemy_team) signature so swapping
        a hover doesn't re-pay the API cost. Result lands in the disk
        cache; the next ``_build_view`` snapshot picks it up via
        ``get_cached``.
        """
        if self._game_plan_llm is None or not self._game_plan_llm.enabled:
            return
        champ = view.my_champion_key
        role = str(view.my_champion_role or "")
        if not champ:
            return
        session = self._latest_session
        allies = self._team_keys_from_session(session, ally=True)
        enemies = self._team_keys_from_session(session, ally=False)
        sig = f"{champ}|{role}|{','.join(sorted(allies))}|{','.join(sorted(enemies))}"
        if sig == self._game_plan_prefetched_for:
            return
        self._game_plan_prefetched_for = sig
        try:
            asyncio.ensure_future(
                self._game_plan_llm.prefetch(
                    champion=champ, role=role,
                    allies=allies, enemies=enemies,
                )
            )
        except RuntimeError:
            # No running event loop (e.g. headless tests) — skip silently.
            pass

    def _maybe_fetch_profiles(self, session: ChampSelectSession) -> None:
        """If a Riot API key is configured, fire off async profile lookups
        for both teams. Empty/no-key/no-puuid → noop. Cached results
        show up on the next view rebuild without blocking session
        rendering.

        Subphase-gated to keep Riot dev-key quota sane:
          * BAN_PICK / PLANNING: enemy team only (~20 API calls).
            Ally identities aren't shown anywhere during the draft.
          * FINALIZATION / loading: both teams (~36 calls). The
            LiveCompanion roster panel surfaces ally mains/WR/last-10
            during the loading-screen window (v1.10.103 — closes the
            b53fa9e feature ask). Fetching during finalization gives
            data a few seconds to land before the loading screen.

        Rate-limit reality: personal Riot dev keys allow 100 / 2 min,
        so one champ-select fits with margin. Failures degrade silently
        to empty profiles via the existing ProfileService error path.
        Local player is always skipped — no point fetching our own
        profile.
        """
        if self._profile_service is None or not getattr(
            self._profile_service, "enabled", False
        ):
            return
        for member in session.their_team:
            self._schedule_profile_fetch(member, is_ally=False)
        # Ally fetching gated on subphase. ``display_subphase`` returns
        # one of the documented strings — finalization / loading both
        # surface the roster panel.
        subphase = session.display_subphase()
        if subphase in ("finalization", "loading"):
            local_cell = session.local_player_cell_id
            for member in session.my_team:
                if member.cell_id == local_cell:
                    continue  # don't fetch our own profile
                self._schedule_profile_fetch(member, is_ally=True)
        # ``/lol-champ-select/v1/session`` privacy-strips puuid +
        # summoner_id from all 9 non-local cells, so the loop above
        # only fetches our own profile (and we skip it). Gameflow
        # session would be the canonical replacement, but its
        # ``gameData.teamOne/teamTwo`` payload still reflects the
        # PREVIOUS game while gameflow.phase == "ChampSelect" — we
        # confirmed this in v1.10.128: ``session_champ_ids`` and
        # ``gameflow_champ_ids`` shared only 2 of 10 entries because
        # gameflow was still serving the just-finished match.
        # Fire the gameflow fetch on champ-select-end instead (see
        # ``handle_event`` ``session_ended`` branch).

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
        # Inflight key is team-prefixed so the same puuid being fetched
        # for ally + enemy doesn't collide (rare but possible in test
        # fixtures). Coalescer handles dedup + RuntimeError-on-no-loop +
        # discard-on-completion in one place.
        inflight_key = f"{'a' if is_ally else 'e'}:{key}"
        self._profile_coalescer.schedule(
            inflight_key,
            lambda: self._fetch_one_profile(member, is_ally=is_ally),
        )

    async def _fetch_one_profile(
        self,
        member: TeamMember,
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
            # During the loading screen, ``_latest_session`` is None (LCU
            # has already deleted the champ-select session), so we have
            # to fall through to the cached ``_loading_screen_session``
            # — otherwise the RosterWindow keeps rendering empty rows
            # even after profiles finish fetching.
            active_session = self._latest_session or self._loading_screen_session
            if active_session is not None:
                self._push_view(self._build_view(active_session))
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "profile_fetch_failed cell=%d ally=%s: %s",
                member.cell_id, is_ally, exc,
            )
        # Inflight discard handled by the Coalescer wrapper.

    async def _fetch_gameflow_session_ids(self) -> None:
        """Recover puuids stripped from champ-select via the gameflow
        session endpoint. Polls the LCU up to a small fixed budget
        because gameflow's ``gameData.teamOne/teamTwo`` only repopulates
        with the *current* match once ``gameflow.phase`` transitions out
        of ``ChampSelect`` — fetching too early returns stale data from
        the previous game (verified in v1.10.128 logs: only 2 of 10
        championIds matched because gameflow was still serving the
        just-finished match).
        """
        from champ_assistant.lcu.client import LcuClient, LcuClientError
        from champ_assistant.lcu.lockfile import (
            LockfileNotFound,
            find_lockfile,
            parse_lockfile,
        )

        # Use the cached just-deleted session for cell→champ-id mapping.
        # ``_latest_session`` is None at this point (session_ended path).
        session = self._loading_screen_session or self._latest_session
        if session is None:
            return
        try:
            lockfile = parse_lockfile(find_lockfile())
        except LockfileNotFound:
            logger.info("gameflow_fetch_skipped reason=lockfile_missing")
            return

        # Poll up to 10 times spaced 1s apart — the gameflow phase flip
        # from ChampSelect → GameStart usually lands within 2-4s of the
        # champ-select-session delete event. 10s budget is generous and
        # bounded; if it never transitions we abandon silently.
        FRESH_PHASES = {"GameStart", "InProgress"}
        data: dict[str, object] | None = None
        attempts = 0
        async with LcuClient(lockfile) as lcu:
            for attempt in range(10):
                attempts = attempt + 1
                try:
                    response = await lcu.get("/lol-gameflow/v1/session")
                except LcuClientError as exc:
                    logger.info("gameflow_fetch_failed: %s", exc)
                    return
                if response.status_code != 200:
                    logger.info(
                        "gameflow_fetch_status status=%d",
                        response.status_code,
                    )
                    return
                try:
                    payload = response.json()
                except ValueError as exc:
                    logger.info("gameflow_fetch_decode_failed: %s", exc)
                    return
                phase = payload.get("phase")
                if phase in FRESH_PHASES:
                    data = payload
                    break
                # Stale phase — wait for the LCU to flip then retry.
                await asyncio.sleep(1.0)
        if data is None:
            logger.info(
                "gameflow_fetch_phase_never_fresh attempts=%d",
                attempts,
            )
            return

        # One-shot dump of the full gameflow response so we can see every
        # field LCU exposes. Riot rejected the puuids we passed with
        # "Exception decrypting" — but their API likely accepts gameName
        # / tagLine via account-v1 if the LCU surfaces them. Dump once
        # per app run to avoid filling the log dir with duplicates.
        if not getattr(self, "_gameflow_payload_dumped", False):
            try:
                import json as _json
                from datetime import datetime as _dt
                from . import app_paths as _app_paths
                log_dir = _app_paths.log_dir()
                log_dir.mkdir(parents=True, exist_ok=True)
                stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                dump_path = log_dir / f"gameflow_dump_{stamp}.json"
                dump_path.write_text(
                    _json.dumps(data, indent=2, default=str),
                    encoding="utf-8",
                )
                self._gameflow_payload_dumped = True
                logger.info("gameflow_payload_dumped path=%s", dump_path)
            except OSError as exc:
                logger.info("gameflow_payload_dump_failed: %s", exc)

        game_data = data.get("gameData") or {}
        members_by_champ_id: dict[int, dict[str, object]] = {}
        for team_key in ("teamOne", "teamTwo"):
            team = game_data.get(team_key) or []
            for entry in team:
                if not isinstance(entry, dict):
                    continue
                cid = entry.get("championId")
                if isinstance(cid, int) and cid > 0:
                    members_by_champ_id[cid] = entry

        if not members_by_champ_id:
            logger.info(
                "gameflow_fetch_empty phase=%r",
                data.get("phase"),
            )
            return

        local_cell = session.local_player_cell_id
        ally_cells = {m.cell_id for m in session.my_team}
        # Per-iteration counters so we can tell *why* the scheduled
        # count was 0: skipped-as-local, no-champ-id, or no-match.
        skipped_local = skipped_no_champ = skipped_no_match = 0
        session_champ_ids = sorted(
            m.champion_id for m in session.my_team + session.their_team
        )
        gameflow_champ_ids = sorted(members_by_champ_id.keys())
        scheduled = 0

        # Step 1 — get our OWN real puuid via the LCU. The local
        # ``current-summoner`` endpoint exposes the user's real
        # 78-character encrypted puuid (in contrast to the synthetic
        # UUID puuids gameflow surfaces for opponents).
        my_puuid = ""
        cs_status = -1
        cs_keys: list[str] = []
        cs_puuid_len = 0
        async with LcuClient(lockfile) as lcu:
            try:
                cs_resp = await lcu.get("/lol-summoner/v1/current-summoner")
                cs_status = cs_resp.status_code
                if cs_resp.status_code == 200:
                    cs_data = cs_resp.json()
                    if isinstance(cs_data, dict):
                        cs_keys = sorted(cs_data.keys())
                        candidate = cs_data.get("puuid")
                        if isinstance(candidate, str):
                            cs_puuid_len = len(candidate)
                            if len(candidate) > 60:
                                my_puuid = candidate
                        # One-shot dump so we can inspect every field
                        # the endpoint exposes (only when the puuid
                        # didn't pass the length check — successful
                        # runs don't need the dump noise).
                        if not my_puuid and not getattr(
                            self, "_current_summoner_dumped", False,
                        ):
                            try:
                                import json as _json
                                from datetime import datetime as _dt
                                from . import app_paths as _app_paths
                                log_dir = _app_paths.log_dir()
                                log_dir.mkdir(parents=True, exist_ok=True)
                                stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
                                dump_path = log_dir / (
                                    f"current_summoner_dump_{stamp}.json"
                                )
                                dump_path.write_text(
                                    _json.dumps(cs_data, indent=2, default=str),
                                    encoding="utf-8",
                                )
                                self._current_summoner_dumped = True
                                logger.info(
                                    "current_summoner_dumped path=%s",
                                    dump_path,
                                )
                            except OSError as exc:
                                logger.info(
                                    "current_summoner_dump_failed: %s", exc,
                                )
            except (LcuClientError, ValueError) as exc:
                logger.info("current_summoner_lookup_failed: %s", exc)
        if not my_puuid:
            logger.info(
                "gameflow_fetch_aborted reason=no_local_puuid "
                "status=%d keys=%s puuid_len=%d",
                cs_status, cs_keys, cs_puuid_len,
            )
            return

        # Step 2 — Riot Web API spectator-v5 returns every participant
        # in our active match with their REAL puuid + championId. Five
        # retries spaced 2s apart because the spectator service can lag
        # the gameflow phase flip by a few seconds.
        participants_by_champ_id: dict[int, dict[str, object]] = {}
        if self._profile_service is None:
            logger.info("gameflow_fetch_aborted reason=no_profile_service")
            return
        riot_client = getattr(self._profile_service, "_client", None)
        if riot_client is None:
            logger.info("gameflow_fetch_aborted reason=no_riot_client")
            return
        for spec_attempt in range(5):
            participants = await riot_client.active_game_participants(my_puuid)
            for entry in participants:
                cid = entry.get("championId")
                puuid = entry.get("puuid")
                if isinstance(cid, int) and cid > 0 and \
                        isinstance(puuid, str) and len(puuid) > 60:
                    participants_by_champ_id[cid] = entry
            if participants_by_champ_id:
                break
            await asyncio.sleep(2.0)
        if not participants_by_champ_id:
            logger.info(
                "spectator_no_active_game attempts=5",
            )
            return

        # Step 3 — map cell_id → real puuid via championId. gameflow's
        # cell-side mapping isn't strictly needed anymore, but keep
        # iterating over the champ-select session so we honor cell-
        # local skip rules and the is_ally classification.
        for member in list(session.my_team) + list(session.their_team):
            if member.cell_id == local_cell:
                skipped_local += 1
                continue
            if member.champion_id == 0:
                skipped_no_champ += 1
                continue
            spec_member = participants_by_champ_id.get(member.champion_id)
            if spec_member is None:
                skipped_no_match += 1
                continue
            real_puuid = str(spec_member.get("puuid") or "")
            try:
                synthetic = TeamMember.model_validate({
                    "cellId": member.cell_id,
                    "championId": member.champion_id,
                    "summonerId": None,
                    "puuid": real_puuid,
                })
            except ValidationError as exc:
                logger.info(
                    "gameflow_synth_member_failed cell=%d: %s",
                    member.cell_id, exc,
                )
                continue
            self._schedule_profile_fetch(
                synthetic, is_ally=member.cell_id in ally_cells,
            )
            scheduled += 1

        logger.info(
            "gameflow_fetch_done resolved=%d scheduled=%d "
            "skipped_local=%d skipped_no_champ=%d skipped_no_match=%d "
            "session_champ_ids=%s gameflow_champ_ids=%s",
            len(members_by_champ_id), scheduled,
            skipped_local, skipped_no_champ, skipped_no_match,
            session_champ_ids, gameflow_champ_ids,
        )

    def _schedule_runtime_fetch(self, enemy_key: str, role: Role) -> None:
        """Kick off a Groq fetch in the background; re-renders on success.

        Idempotent — returns early if a fetch for this matchup is already
        in flight or if the runtime store isn't enabled (no API key).
        """
        store = self._runtime_counters
        if store is None or not store.enabled:
            return
        key = (enemy_key, role)

        async def _fetch_and_rerender() -> None:
            try:
                counters = await store.get(enemy_key, role)
                if counters and self._latest_session is not None:
                    view = self._build_view(self._latest_session)
                    self._push_view(view)
            except Exception:  # noqa: BLE001
                logger.exception("runtime_fetch_failed")
            # Inflight discard handled by the Coalescer wrapper.

        # Coalescer dedups + handles RuntimeError-on-no-loop + discards
        # the key on completion. Returns False if the fetch was already
        # inflight or no loop is running — in either case nothing to do.
        self._runtime_coalescer.schedule(key, _fetch_and_rerender)


    # -- Live data updates ------------------------------------------------

    def notify_game_active(self) -> None:
        """LCDA reports an active game (game_time > 0) — clear the
        loading-screen flag so the RosterWindow hides. Called once per
        game from boot.py's LCDA watcher on the off→on transition."""
        if not self._loading_screen_active:
            return
        self._loading_screen_active = False
        self._loading_screen_session = None
        # Game is live → drop the profile caches we kept around for the
        # loading screen. Next champ-select will repopulate them.
        self._enemy_profiles_by_cell.clear()
        self._ally_profiles_by_cell.clear()
        if self._profile_service is not None:
            self._profile_service.clear()
        view = self._build_view(self._latest_session)
        self._push_view(view)

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
