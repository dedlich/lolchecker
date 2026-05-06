"""CLI entry point.

Phase 6 wires everything: argparse → LcuSource → ChampAssistant → MainOverlay,
running on a qasync event loop so PyQt6 + asyncio share one loop.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
from collections.abc import Callable
from pathlib import Path

import qasync
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

# Absolute imports (not relative) so PyInstaller can run this file directly
# as the entry point. PyInstaller executes __main__.py without setting
# __package__, which would break `from .app import ...`. Absolute imports
# work in both modes — `python -m champ_assistant` AND a frozen exe.
from champ_assistant.app import ChampAssistant
from champ_assistant.data.loader import (
    DataLoadError,
    load_builds,
    load_counters,
    load_tags,
    load_tiers,
)
from champ_assistant.data.models import BuildLibrary, Champion
from champ_assistant.data.runtime_counters import RuntimeCounterStore
from champ_assistant.lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource
from champ_assistant.lifecycle import LifecycleManager
from champ_assistant.logging_setup import install_tag_filter, make_formatter
from champ_assistant.safety import CrashHandler
from champ_assistant.ui.overlay import MainOverlay


def _log_startup_summary(perf_module) -> None:  # type: ignore[no-untyped-def]
    """Emit a single-line summary of the boot timeline so the user
    (or grep) can quickly see where startup time went without parsing
    performance.log. Best-effort — never raises."""
    try:
        records = perf_module.monitor().snapshot()
        if not records:
            return
        summary = ", ".join(f"{r.name}={r.elapsed_ms:.0f}ms" for r in records)
        total = records[-1].elapsed_ms
        log = logging.getLogger("champ_assistant.startup")
        log.info("startup_complete total=%.0fms phases=[%s]", total, summary)
    except Exception:  # diagnostics must never crash
        pass


def _resource_root() -> Path:
    """Thin delegator to ``app_paths.resource_root`` — kept for backwards
    compat with existing call sites in this module."""
    from champ_assistant import app_paths
    return app_paths.resource_root()


DEFAULT_FIXTURE_DIR = _resource_root() / "tests" / "fixtures" / "sessions"
DEFAULT_DATA_DIR = _resource_root() / "data"


# Bootstrap-only champion dict. The orchestrator needs SOME champion table
# at construction time, before the async DataDragon hydration in
# _hydrate_champions_and_icons replaces it with the live ~170-champion
# roster. This 30-champion list IS NOT the production source of truth —
# any session that runs past hydration sees the full DataDragon list.
#
# Hydration failure (offline + empty cache) falls back to this list and
# logs a loud warning — see "DEGRADED" path in
# _hydrate_champions_and_icons. State invariant: by the time the user
# is in champ-select, ``assistant.champions`` should have grown past
# this list. ``docs/OPTIMIZATION.md §1.4`` proposes routing everything
# through ``data.datadragon.load_champion_index()`` (sync API) and
# failing loud on empty cache; tracked there as future work.
_STARTER_CHAMPIONS: list[Champion] = [
    Champion(id=1, key="Annie", name="Annie", tags=["Mage"]),
    Champion(id=3, key="Galio", name="Galio", tags=["Tank", "Mage"]),
    Champion(id=7, key="LeBlanc", name="LeBlanc", tags=["Assassin", "Mage"]),
    Champion(id=16, key="Soraka", name="Soraka", tags=["Support"]),
    Champion(id=21, key="MissFortune", name="Miss Fortune", tags=["Marksman"]),
    Champion(id=22, key="Ashe", name="Ashe", tags=["Marksman", "Support"]),
    Champion(id=51, key="Caitlyn", name="Caitlyn", tags=["Marksman"]),
    Champion(id=53, key="Blitzcrank", name="Blitzcrank", tags=["Tank", "Fighter"]),
    Champion(id=60, key="Elise", name="Elise", tags=["Mage", "Fighter"]),
    Champion(id=64, key="Lee Sin", name="Lee Sin", tags=["Fighter", "Assassin"]),
    Champion(id=67, key="Vayne", name="Vayne", tags=["Marksman", "Assassin"]),
    Champion(id=76, key="Nidalee", name="Nidalee", tags=["Assassin", "Fighter"]),
    Champion(id=81, key="Ezreal", name="Ezreal", tags=["Marksman", "Mage"]),
    Champion(id=86, key="Garen", name="Garen", tags=["Fighter", "Tank"]),
    Champion(id=89, key="Leona", name="Leona", tags=["Tank", "Support"]),
    Champion(id=90, key="Malzahar", name="Malzahar", tags=["Mage", "Assassin"]),
    Champion(id=103, key="Ahri", name="Ahri", tags=["Mage", "Assassin"]),
    Champion(id=111, key="Nautilus", name="Nautilus", tags=["Tank", "Fighter"]),
    Champion(id=117, key="Lulu", name="Lulu", tags=["Support", "Mage"]),
    Champion(id=120, key="Hecarim", name="Hecarim", tags=["Fighter", "Tank"]),
    Champion(id=122, key="Darius", name="Darius", tags=["Fighter", "Tank"]),
    Champion(id=145, key="Kaisa", name="Kai'Sa", tags=["Marksman"]),
    Champion(id=157, key="Yasuo", name="Yasuo", tags=["Fighter", "Assassin"]),
    Champion(id=164, key="Camille", name="Camille", tags=["Fighter", "Assassin"]),
    Champion(id=222, key="Jinx", name="Jinx", tags=["Marksman"]),
    Champion(id=234, key="Viego", name="Viego", tags=["Fighter", "Assassin"]),
    Champion(id=412, key="Thresh", name="Thresh", tags=["Tank", "Support"]),
    Champion(id=711, key="Vex", name="Vex", tags=["Mage"]),
    Champion(id=875, key="Sett", name="Sett", tags=["Fighter", "Tank"]),
    Champion(id=897, key="KSante", name="K'Sante", tags=["Tank", "Fighter"]),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="champ-assistant",
        description="LoL Champ Select Assistant — counters & pick suggestions.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Run without a real League client; replay JSON fixtures.")
    parser.add_argument("--fixture", type=Path, default=None,
                        help=f"Fixture file or directory (default: {DEFAULT_FIXTURE_DIR})")
    parser.add_argument("--cycle", action="store_true",
                        help="Cycle through all fixtures (with --dry-run).")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="Seconds between fixture cycles (default: 5).")
    parser.add_argument("--stress", action="store_true",
                        help="Emit randomized state updates (with --dry-run).")
    parser.add_argument("--rate", type=float, default=10.0,
                        help="Stress-mode update rate in Hz (default: 10).")
    parser.add_argument("--demo-recommendations", action="store_true",
                        help="Render all decision-engine rules in the "
                             "recommendation panel for visual testing "
                             "(no live game required).")
    parser.add_argument("--no-ui", action="store_true",
                        help="Skip the Qt window (print events instead).")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"Static data directory (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    # Bootstrap installer args — hidden from --help; set by apply_update when
    # launching the newly extracted exe to install itself.
    parser.add_argument("--bootstrap-staged", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-install", metavar="DIR", help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap-parent-pid", type=int, metavar="PID",
                        help=argparse.SUPPRESS)
    return parser


def _make_source(args: argparse.Namespace) -> LcuSource:
    if args.dry_run:
        fixture = args.fixture or DEFAULT_FIXTURE_DIR
        return FixtureLcuSource(
            fixture, cycle=args.cycle, stress=args.stress,
            interval=args.interval, rate=args.rate,
        )
    return RealLcuSource()


def _starter_champion_index() -> dict[int, Champion]:
    return {c.id: c for c in _STARTER_CHAMPIONS}


def _build_assistant(args: argparse.Namespace, overlay: MainOverlay) -> ChampAssistant:
    # Builds are optional — older bundles may not ship builds.json. Default
    # to an empty BuildLibrary so PickCards render without runes/items.
    builds: BuildLibrary
    try:
        builds = load_builds(args.data_dir / "builds.json")
    except DataLoadError:
        builds = BuildLibrary()

    # Runtime counter fetching is opt-in via GROQ_API_KEY (free tier at
    # https://console.groq.com). Without a key the store is constructed
    # disabled and never makes a network call — falls back to seed data.
    cache_dir = args.data_dir.parent / "ddragon_cache" / "runtime_counters"
    from champ_assistant import secrets as _sec
    runtime_counters = RuntimeCounterStore(
        cache_dir,
        api_key=_sec.llm_api_key(),
        provider=_sec.llm_provider(),
    )

    # Enemy profiling — opt-in via Settings dialog (Riot API key persisted
    # in keyring). Disabled service falls through silently.
    profile_service = _build_profile_service()

    return ChampAssistant(
        source=_make_source(args),
        overlay=overlay,
        counters=load_counters(args.data_dir / "counters.json"),
        tiers=load_tiers(args.data_dir / "tiers.json"),
        tags=load_tags(args.data_dir / "tags.json"),
        champions=_starter_champion_index(),
        builds=builds,
        runtime_counters=runtime_counters,
        profile_service=profile_service,
    )


def _build_profile_service():  # type: ignore[no-untyped-def]
    """Construct a ProfileService from persisted keyring credentials."""
    from champ_assistant import secrets
    from champ_assistant.profiling import ProfileService, RiotApiClient

    api_key = secrets.riot_api_key()
    region = secrets.riot_region()
    client = RiotApiClient(api_key, region=region)
    return ProfileService(client)


def _enable_gpu_backend() -> None:
    """Force the desktop OpenGL backend before QApplication is created.

    Qt's default on Windows is the ANGLE/software fallback for
    older drivers; on a modern Win10/11 box with a GPU the desktop-OpenGL
    backend is faster and lower-CPU for a translucent always-on-top
    window. Has to be set BEFORE QApplication() — once the app exists
    it's too late.
    """
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
    # Smooth Z-order updates for layered windows; harmless on others.
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # Per-screen DPI changes (multi-monitor with mixed scaling) — Qt6 default,
    # but we set it explicitly so a future Qt5 backport stays correct.
    fmt = QSurfaceFormat()
    fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
    QSurfaceFormat.setDefaultFormat(fmt)


def _safe_start(name: str, fn: Callable[[], None]) -> bool:
    """Run a subsystem's start hook isolated from the rest of init.

    A misbehaving subsystem (e.g. psutil import error in diagnostics, a
    Win32 RegisterHotKey returning 1409 because another app already grabbed
    the same combo) must NOT prevent the main app window from opening.
    Logs the failure with a [STARTUP] tag so the production log keeps a
    clear trace of which subsystems came up and which were skipped.
    """
    log = logging.getLogger("champ_assistant.lifecycle")
    try:
        fn()
        log.info("subsystem started: %s", name)
        return True
    except Exception:  # subsystem must not crash the app
        log.exception("subsystem start failed: %s — continuing degraded", name)
        return False


def _run_with_ui(args: argparse.Namespace) -> int:
    # ------------------------------------------------------------------
    # Deterministic startup (P7): each subsystem is registered with the
    # lifecycle manager in the same order it is brought up. Teardown
    # walks the list in reverse on aboutToQuit so producers stop before
    # consumers (e.g. hotkey listener stops before its signal target's
    # Qt loop shuts down).
    # Canonical order: logging (already up) → state → window → layout
    # → hotkeys → update → render.
    # ------------------------------------------------------------------
    from champ_assistant import performance_monitor as _perf
    _perf.record_phase("run_with_ui_entry")
    lifecycle = LifecycleManager()

    # ------------------------------------------------------------------
    # Failure-recovery layer: detect Safe Mode from disk markers BEFORE
    # any subsystem starts so we know which subsystems to gate. Always
    # consume the prior clean_shutdown.marker — its job is to cover
    # ONE shutdown only; carrying it forward would mask future crashes.
    # ------------------------------------------------------------------
    from champ_assistant import overlay_config as _ovc
    from champ_assistant import safe_mode as _safe_mode
    from champ_assistant.session_summary import UptimeClock
    startup_mode = _safe_mode.decide_startup_mode()
    _safe_mode.consume_clean_shutdown_marker()
    uptime_clock = UptimeClock()
    # Persisted overlay-config drives every feature toggle below
    # (telemetry, update-check, vision services, widget visibility).
    # Load once at the top so all the gating blocks read consistent
    # state instead of re-loading multiple times.
    persisted = _ovc.load()
    # Low Resource Mode (charter A5): single master switch that forces
    # every optional subsystem off + reduces render rate for low-end
    # hardware / streaming scenarios. Applied as a runtime override
    # so the user's per-feature flags stay as they set them — toggling
    # LRM off restores the original preferences on next launch.
    if persisted.low_resource_mode:
        logging.getLogger(__name__).info(
            "low_resource_mode active — telemetry/update-check/vision disabled",
        )
        persisted.enable_telemetry = False
        persisted.enable_update_check = False
        persisted.enable_auto_camp_detection = False
        persisted.enable_scoreboard_detection = False
    if startup_mode.safe:
        logging.getLogger(__name__).warning(
            "safe_mode active: %s — hotkeys/telemetry/update_check disabled",
            startup_mode.reason,
        )

    _enable_gpu_backend()
    qt_app = QApplication(sys.argv[:1])

    overlay = MainOverlay(load_persisted_state=True)
    _perf.record_phase("overlay_created")
    # System tray icon — only way back to the main overlay during a game
    # (overlay mode hides the main window). Held as instance attr so Qt
    # doesn't garbage-collect it.
    from champ_assistant.ui.tray import TrayController
    overlay._tray = TrayController(overlay)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Production-grade infrastructure (v0.12.0 architecture upgrade):
    #   * StateStore  — single immutable source of truth
    #   * RenderScheduler — coalesces repaints + 1 Hz tick
    #   * Diagnostics — periodic CPU/FPS/latency logging
    # The store + scheduler sit ABOVE the existing widget interfaces;
    # widgets keep their update_view/update_snapshot APIs but get driven
    # via store subscriptions instead of direct LCDA-source callbacks.
    # ------------------------------------------------------------------
    from champ_assistant.diagnostics import Diagnostics
    from champ_assistant.render_scheduler import RenderScheduler
    from champ_assistant.state_store import StateStore
    store = StateStore()
    # Low Resource Mode caps the repaint cadence to ~10 FPS — well below
    # human perception of stutter at the cost of saving ~3x the CPU on
    # idle frames. Default 30 FPS otherwise.
    _scheduler_fps = 10 if persisted.low_resource_mode else RenderScheduler.DEFAULT_MAX_FPS
    scheduler = RenderScheduler(max_fps=_scheduler_fps)
    diagnostics = Diagnostics()
    lifecycle.register("scheduler", scheduler.stop)
    lifecycle.register("diagnostics", diagnostics.stop)
    diagnostics.attach_scheduler(scheduler)
    diagnostics.attach_store(store)
    from champ_assistant import health_monitor as _health_global
    diagnostics.attach_health_monitor(_health_global.monitor())

    # Performance baseline (charter step A1 — Fastest). Records named
    # phase timestamps from process start so we can audit cold-start
    # time and service-init time after the fact. Detection-only;
    # optimization decisions belong elsewhere.
    from champ_assistant import performance_monitor as _perf
    _perf.record_phase("core_services_initialized")
    lifecycle.register("performance_log", lambda: _perf.monitor().flush())
    # Per-rule eval-time digest — surfaces which decision-engine rules
    # spent the most CPU during the session. Useful for spotting a slow
    # rule under live game load (Strategy A2).
    lifecycle.register(
        "rule_timing_log", lambda: _perf.rule_timing_recorder().flush(),
    )

    # State invariant validator (charter step C4 — Most Reliable).
    # Pure observer over the state store; logs timer / game-state
    # invariant violations at warning level. Detection-only — never
    # mutates state. The cost is one validation pass per snapshot,
    # negligible vs the rest of the LCDA tick budget.
    from champ_assistant.state_validator import StateValidator
    state_validator = StateValidator(store)
    lifecycle.register("state_validator", state_validator.stop)

    # Deterministic jungle camp predictor — pure Python, no Qt. Drives
    # the minimap widget's camp row off a fixed-cycle timeline rather
    # than user clicks. Subscribes to lcda_snapshot updates below.
    from champ_assistant.jungle_timeline import JungleTimelineEngine
    jungle_engine = JungleTimelineEngine()

    # Lightweight telemetry recorder — captures discrete UI/state
    # transitions to a JSONL file for offline UX analysis. Singleton,
    # non-blocking on record(), batches disk writes every 5s.
    from champ_assistant import telemetry as _telemetry
    telemetry_recorder = _telemetry.recorder()
    lifecycle.register("telemetry", telemetry_recorder.stop)
    # Subscribe a band-tracker to the engine so confidence-band flips
    # surface as discrete events.
    jungle_engine.subscribe(_telemetry.make_band_tracker())
    # Build the fight-window detector — fed by the LCDA watcher below.
    fight_detector = _telemetry.make_fight_window_detector()
    overlay._store = store              # type: ignore[attr-defined]
    overlay._scheduler = scheduler      # type: ignore[attr-defined]
    overlay._diagnostics = diagnostics  # type: ignore[attr-defined]
    # Drive the embedded power-spike panel's fade animation off the
    # central tick instead of its own QTimer (P5).
    overlay.power_spike_panel.connect_scheduler(scheduler)

    assistant = _build_assistant(args, overlay)
    _perf.record_phase("assistant_built")
    # Wire the clickable enemy-role badge to the orchestrator's cycle method.
    overlay.enemy_role_clicked.connect(assistant.cycle_enemy_role_override)

    # Apply Build: wire the PickCard signal through to two LCU writes:
    # (1) create+activate a rune page; (2) push a custom item set the
    # user can pick in the in-game shop. Each round-trip runs as an
    # async task so the UI never freezes; the status bar shows progress.
    def _on_apply_build(
        champion_key: str, rune_names: list, item_names: list,
    ) -> None:
        async def _run() -> None:
            from champ_assistant.lcu.client import LcuClient, LcuClientError
            from champ_assistant.lcu.item_sets import apply_item_set
            from champ_assistant.lcu.lockfile import (
                LockfileNotFound,
                find_lockfile,
                parse_lockfile,
            )
            from champ_assistant.lcu.perks import apply_rune_page
            try:
                lockfile_path = find_lockfile()
                lockfile = parse_lockfile(lockfile_path)
            except LockfileNotFound:
                overlay.status_bar.set_info(
                    "Apply: League-Client nicht erreichbar",
                    color="#FF6B6B",
                )
                return

            applied: list[str] = []
            had_error = False
            # Look up the champion's numeric id for the item set's
            # associatedChampions field. Without it the set still works
            # but won't be auto-suggested in champion select.
            champ_id = next(
                (c.id for c in assistant.champions.values()
                 if c.key == champion_key),
                0,
            )
            try:
                async with LcuClient(lockfile) as lcu:
                    if rune_names:
                        try:
                            page = await apply_rune_page(
                                lcu,
                                champion_key=champion_key,
                                rune_names=rune_names,
                            )
                            if page is not None:
                                applied.append("Runen")
                        except LcuClientError as exc:
                            logging.getLogger(__name__).warning(
                                "apply_runes_failed: %s", exc,
                            )
                            had_error = True
                    if item_names:
                        try:
                            iset = await apply_item_set(
                                lcu,
                                champion_key=champion_key,
                                champion_id=champ_id,
                                item_names=item_names,
                            )
                            if iset is not None:
                                applied.append("Items")
                        except LcuClientError as exc:
                            logging.getLogger(__name__).warning(
                                "apply_items_failed: %s", exc,
                            )
                            had_error = True
            except LcuClientError as exc:
                overlay.status_bar.set_info(
                    f"Apply Build fehlgeschlagen: {exc}",
                    color="#FF6B6B",
                )
                return

            if not applied:
                overlay.status_bar.set_info(
                    f"Apply Build {champion_key}: nichts angewendet",
                    color="#FFB84A",
                )
            elif had_error:
                overlay.status_bar.set_info(
                    f"Apply Build {champion_key}: nur {' + '.join(applied)}",
                    color="#FFB84A",
                )
            else:
                overlay.status_bar.set_info(
                    f"Apply Build {champion_key}: {' + '.join(applied)} aktiviert",
                    color="#7FCC7F",
                )
        import asyncio
        try:
            asyncio.create_task(_run())
        except RuntimeError:
            pass
    overlay.apply_build_requested.connect(_on_apply_build)

    # Lock pick / lock ban: clicking a suggestion card sends a single
    # LCU PATCH that locks in the chosen champion in the player's
    # current action slot. User preference is direct lock-in (single
    # click → committed) rather than hover + manual confirm.
    def _on_hover_request(champion_key: str, action_type: str) -> None:
        async def _run() -> None:
            from champ_assistant.lcu.champ_select import commit_action
            from champ_assistant.lcu.client import LcuClient, LcuClientError
            from champ_assistant.lcu.lockfile import (
                LockfileNotFound,
                find_lockfile,
                parse_lockfile,
            )

            session = assistant._latest_session
            if session is None:
                overlay.status_bar.set_info(
                    "Hover: keine aktive Champ-Select-Session",
                    color="#FFB84A",
                )
                return

            action = session.my_pending_action(action_type)
            if action is None:
                overlay.status_bar.set_info(
                    f"Hover: keine offene {action_type}-Aktion gerade",
                    color="#FFB84A",
                )
                return

            champ_id = next(
                (c.id for c in assistant.champions.values()
                 if c.key == champion_key),
                0,
            )
            if champ_id == 0:
                overlay.status_bar.set_info(
                    f"Hover: Champion {champion_key} nicht in Registry",
                    color="#FF6B6B",
                )
                return

            try:
                lockfile_path = find_lockfile()
                lockfile = parse_lockfile(lockfile_path)
            except LockfileNotFound:
                overlay.status_bar.set_info(
                    "Hover: League-Client nicht erreichbar",
                    color="#FF6B6B",
                )
                return

            try:
                async with LcuClient(lockfile) as lcu:
                    status = await commit_action(
                        lcu, action_id=action.id, champion_id=champ_id,
                    )
            except LcuClientError as exc:
                logging.getLogger(__name__).warning(
                    "lock_failed: type=%s champ=%s err=%s",
                    action_type, champion_key, exc,
                )
                overlay.status_bar.set_info(
                    f"Lock {champion_key} fehlgeschlagen: {exc}",
                    color="#FF6B6B",
                )
                return

            if 200 <= status < 300:
                verb = "Pick" if action_type == "pick" else "Ban"
                overlay.status_bar.set_info(
                    f"{verb} gesetzt: {champion_key}",
                    color="#7FCC7F",
                )
            else:
                overlay.status_bar.set_info(
                    f"Lock {champion_key}: HTTP {status}",
                    color="#FFB84A",
                )

        import asyncio
        try:
            asyncio.create_task(_run())
        except RuntimeError:
            pass

    overlay.pick_hover_requested.connect(
        lambda key: _on_hover_request(key, "pick")
    )
    overlay.ban_hover_requested.connect(
        lambda key: _on_hover_request(key, "ban")
    )

    # When the user saves a new Riot API key in Settings, rebuild the
    # profile service so subsequent enemy profile lookups use it.
    def _on_settings_changed() -> None:
        assistant._profile_service = _build_profile_service()
        assistant._enemy_profiles_by_cell.clear()
        if assistant._latest_session is not None:
            assistant._push_view(assistant._build_view(assistant._latest_session))
    overlay.settings_changed.connect(_on_settings_changed)

    overlay.show()
    _perf.record_phase("ui_visible")
    _log_startup_summary(_perf)
    # Surface the first-launch welcome banner once the window is up so
    # the fade-in lands on a settled layout. No-op on every subsequent
    # launch (state lives in overlay_config.onboarding_seen).
    overlay.show_onboarding_if_needed()

    # Safe-mode banner: surface in the status bar's persistent info slot
    # with a "Resume Normal" affordance. Click → clear crash report,
    # write marker, restart will boot normal regardless of this session.
    if startup_mode.safe:
        def _on_resume_normal() -> None:
            _safe_mode.resume_normal_mode()
            overlay.status_bar.dismiss_safe_mode_banner()
        overlay.status_bar.show_safe_mode_banner(on_resume=_on_resume_normal)

    # Application-level focus tracking — captures gain/loss as the user
    # alt-tabs between the game and the overlay. Telemetry-only,
    # no rendering side-effect.
    def _on_app_state(state) -> None:  # type: ignore[no-untyped-def]
        gained = state == Qt.ApplicationState.ApplicationActive
        _telemetry.recorder().record(
            _telemetry.EV_FOCUS,
            {"direction": "gain" if gained else "loss"},
        )
    qt_app.applicationStateChanged.connect(_on_app_state)

    crash = CrashHandler()
    # Surface any swallowed exception in the info slot so silent failures
    # (orchestrator dying, source raising during init, etc.) stay visible
    # instead of being overwritten by the next connection-state refresh.
    def _on_crash(msg: str) -> None:
        overlay.status_bar.set_info(f"Error: {msg[:80]}", color="#FF6B6B")
    crash.subscribe(_on_crash)

    # Persist a crash report on every uncaught exception so the next
    # launch can boot in Safe Mode if the prior shutdown wasn't clean.
    # Collector closure pulls best-effort current state via try-blocks
    # so a half-initialized app still produces a partial report.
    from champ_assistant import __version__ as _app_version
    from champ_assistant import crash_report as _crash_report
    from champ_assistant.ui.floating_widget import FloatingWidget as _FloatingWidget

    def _collect_state_snapshot() -> dict:
        try:
            cur = store.get()
            return {
                "phase": cur.phase,
                "connection_state": cur.connection_state,
                "active_widgets": [
                    type(w).__name__ for w in _FloatingWidget._instances
                    if w.isVisible()
                ],
                "last_state_vector": {
                    "phase": cur.phase,
                    "game_time": cur.game_time,
                    "revision": cur.revision,
                },
            }
        except Exception:  # collector must be tolerant
            return {}

    def _on_uncaught(exc_type, exc_value, exc_tb) -> None:
        _crash_report.write_crash_report(
            exc_type, exc_value, exc_tb,
            version=_app_version,
            uptime_seconds=uptime_clock.elapsed(),
            state_collector=_collect_state_snapshot,
        )
    crash.set_uncaught_callback(_on_uncaught)

    loop = qasync.QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    crash.install(loop=loop)
    lifecycle.register("crash_handler", crash.uninstall)

    # Schedule the orchestrator runner via loop.create_task — works on a
    # not-yet-running loop. asyncio.create_task() (and TaskManager.spawn,
    # which wraps it) would raise here because there's no running loop yet.
    consumer = loop.create_task(assistant.run(), name="orchestrator-run")
    # Background side-tasks: champion data + icon prefetch + update notifier.
    icon_task = loop.create_task(
        _hydrate_champions_and_icons(overlay, assistant, args.data_dir),
        name="champion-prefetch",
    )
    if startup_mode.safe:
        # Update checks disabled in Safe Mode — a failed update is one
        # of the things that could have caused the prior crash, and
        # nagging about a new version while the user is trying to
        # diagnose is poor signal-to-noise.
        update_task = loop.create_task(asyncio.sleep(0), name="update-check-skipped")
    elif not persisted.enable_update_check:
        logging.getLogger(__name__).info("update-check disabled (settings)")
        update_task = loop.create_task(asyncio.sleep(0), name="update-check-skipped")
    else:
        update_task = loop.create_task(
            _check_and_notify_update(overlay, lifecycle), name="update-check"
        )
    # Lifecycle entry: cancel async tasks before tearing down the loop so
    # in-flight downloads / icon prefetches abort cleanly instead of
    # raising into qasync's exception handler at shutdown.
    def _cancel_async_tasks() -> None:
        for t in (consumer, icon_task, update_task):
            t.cancel()
    lifecycle.register("async_tasks", _cancel_async_tasks)

    # Floating mini-widgets (Blitz-style independent overlays). Each one is
    # its own top-level transparent always-on-top window with persisted
    # position. Toggled via overlay_config flags in Settings.
    # ``persisted`` was loaded near the top of this function — same
    # state object drives every visibility / feature flag below.
    from champ_assistant.ui.lobby_stats_widget import LobbyStatsWidget
    from champ_assistant.ui.minimap_timers_widget import MinimapTimersWidget
    from champ_assistant.ui.recommendation_panel import RecommendationPanel
    from champ_assistant.ui.scoreboard_widget import ScoreboardWidget
    floating: list[object] = []
    if persisted.show_scoreboard:
        scoreboard = ScoreboardWidget()
        floating.append(scoreboard)
        # Subscribe to state.scoreboard_visible so the panel peeks when
        # vision detects the in-game TAB scoreboard (or when the user
        # presses Ctrl+Alt+B). NO keyboard polling — see
        # tests/lint/test_no_input_hooks.py for the Vanguard rationale.
        def _drive_scoreboard_peek(old, new) -> None:  # type: ignore[no-untyped-def]
            if old.scoreboard_visible != new.scoreboard_visible:
                scoreboard.set_peek_visible(new.scoreboard_visible)
        store.subscribe(_drive_scoreboard_peek)
    minimap = None
    if persisted.show_minimap_timers:
        minimap = MinimapTimersWidget()
        minimap.connect_scheduler(scheduler)
        minimap.attach_engine(jungle_engine)
        floating.append(minimap)
    # Recommendation panel — surfaces decision_engine output as
    # severity-sorted on-screen hints. Always shown (it self-hides
    # when no recs are active), so we can validate behavior in real
    # games. Demo mode pre-fills with examples for visual testing.
    recommendation_panel = RecommendationPanel()
    recommendation_panel.set_focus_mode(persisted.focus_mode)
    floating.append(recommendation_panel)

    # InsightPanel — detail-view of the top recommendation. Hidden by
    # default; toggled via Ctrl+Alt+I global hotkey.
    from champ_assistant.ui.insight_panel import InsightPanel
    insight_panel = InsightPanel()
    # Track the latest top-rec so InsightPanel.toggle() always opens
    # with the most current recommendation, not a stale one.
    _latest_top_rec: list = [None]
    floating.append(insight_panel)
    if getattr(args, "demo_recommendations", False):
        recommendation_panel.populate_demo()
        recommendation_panel.show()
    lobby_stats: LobbyStatsWidget | None = None
    if persisted.show_lobby_stats:
        lobby_stats = LobbyStatsWidget()
        # Hooked into the SessionView pipeline below (not LCDA).
        overlay._lobby_stats = lobby_stats  # type: ignore[attr-defined]

    # Bridge LCDA snapshots into the jungle timeline. The engine is
    # purely deterministic — it just needs game_time + the cumulative
    # event log to bump confidence anchors.
    def _drive_jungle_engine(old, new) -> None:  # type: ignore[no-untyped-def]
        snap = new.lcda_snapshot
        if snap is None:
            return
        events = list(getattr(snap, "raw_events", []) or [])
        jungle_engine.tick(snap.game_time, events)
        # Same hook drives the fight-window detector — uses the
        # cumulative event list, edge-triggered emit on transition.
        fight_detector(events)
    store.subscribe(_drive_jungle_engine)

    # Phase change telemetry — emit on every phase transition so the
    # offline summary can derive early/mid/late timing.
    def _track_phase_changes(old, new) -> None:  # type: ignore[no-untyped-def]
        if old.phase != new.phase:
            _telemetry.recorder().record(
                _telemetry.EV_GAME_PHASE_CHANGE,
                {"from": old.phase, "to": new.phase, "game_time": new.game_time},
            )
        if old.main_visible != new.main_visible:
            _telemetry.recorder().record(
                _telemetry.EV_OVERLAY_TOGGLE,
                {"visible": new.main_visible},
            )
    store.subscribe(_track_phase_changes)

    lcda_task = loop.create_task(
        _run_lcda_watcher(
            overlay, floating,
            champions=assistant.champions,
            tags=assistant.tags,
            cache_dir=args.data_dir.parent / "ddragon_cache",
            assistant=assistant,
        ),
        name="lcda-watcher",
    )
    lifecycle.register("lcda_task", lcda_task.cancel)

    # Start production-grade infrastructure now that the loop is set up.
    # Each start is isolated — a misbehaving subsystem must not block the
    # rest of init (subsystem isolation, P2).
    _safe_start("scheduler", scheduler.start)
    if persisted.diagnostics_enabled:
        _safe_start("diagnostics", diagnostics.start)
    else:
        logging.getLogger(__name__).info("diagnostics disabled via settings")
    if startup_mode.safe:
        # Telemetry intentionally disabled in Safe Mode — the
        # recorder's batch flush touches disk and could interact with
        # whatever caused the prior crash.
        logging.getLogger(__name__).info("telemetry disabled (safe mode)")
    elif not persisted.enable_telemetry:
        logging.getLogger(__name__).info("telemetry disabled (settings)")
    else:
        _safe_start("telemetry", telemetry_recorder.start)

    # ------------------------------------------------------------------
    # Vision subsystem (Stage A — color heuristic camp detection).
    # Triple-gated: settings flag + Safe Mode off + Windows-only check
    # inside MinimapCapture. Construction is cheap (no thread spawn);
    # start() is the actual side-effect.
    # ------------------------------------------------------------------
    vision_service = None
    if persisted.enable_auto_camp_detection and not startup_mode.safe:
        from champ_assistant.vision.observation_service import VisionObservationService

        def _vision_game_time() -> float | None:
            cur = store.get()
            return cur.game_time if cur.lcda_snapshot is not None else None

        vision_service = VisionObservationService(game_time_provider=_vision_game_time)

        # Engine sync via Qt signal — main-thread call into engine.
        from PyQt6.QtCore import Qt as _VQt
        def _on_vision_clear(camp_id: str, gt: float, _conf: float) -> None:
            jungle_engine.register_clear(camp_id, gt)
        vision_service.camp_cleared.connect(
            _on_vision_clear, _VQt.ConnectionType.QueuedConnection,
        )

        # Diagnostics integration — counters appear in the [DIAG] line.
        diagnostics.attach_vision(vision_service)
        _safe_start("vision", vision_service.start)
        lifecycle.register("vision", vision_service.stop)
    else:
        if not persisted.enable_auto_camp_detection:
            logging.getLogger(__name__).info(
                "vision disabled (enable_auto_camp_detection=False in settings)"
            )

    # ------------------------------------------------------------------
    # Scoreboard visibility vision service — independent worker thread,
    # writes state.scoreboard_visible into the StateStore on transition.
    # Same triple-gate as camp detection: settings + safe-mode-off +
    # Windows-only check inside MinimapCapture.
    # ------------------------------------------------------------------
    scoreboard_visibility_service = None
    scoreboard_overlay_panel = None
    scoreboard_overlay_controller = None
    if persisted.enable_scoreboard_detection and not startup_mode.safe:
        from PyQt6.QtCore import Qt as _SBQt
        from champ_assistant.vision.scoreboard_visibility_service import (
            ScoreboardVisibilityService,
        )
        from champ_assistant.ui.scoreboard_overlay import (
            GoldDifferencePanel,
            ScoreboardOverlayController,
        )

        scoreboard_visibility_service = ScoreboardVisibilityService()

        # Vision thread → main thread state-store update via queued
        # signal. Engine-side mutation always lands on the Qt main
        # thread (StateStore is technically thread-safe but we keep
        # all writes on one thread for predictability).
        def _on_scoreboard_visibility(visible: bool) -> None:
            store.update(scoreboard_visible=visible)

        scoreboard_visibility_service.visibility_changed.connect(
            _on_scoreboard_visibility, _SBQt.ConnectionType.QueuedConnection,
        )

        scoreboard_overlay_panel = GoldDifferencePanel()
        scoreboard_overlay_controller = ScoreboardOverlayController(
            state_store=store, panel=scoreboard_overlay_panel,
        )

        _safe_start("scoreboard_visibility", scoreboard_visibility_service.start)
        lifecycle.register("scoreboard_visibility", scoreboard_visibility_service.stop)
        lifecycle.register("scoreboard_overlay", scoreboard_overlay_controller.stop)
    else:
        if not persisted.enable_scoreboard_detection:
            logging.getLogger(__name__).info(
                "scoreboard detection disabled (enable_scoreboard_detection=False)"
            )

    # ------------------------------------------------------------------
    # Global hotkeys (Win32 RegisterHotKey via dedicated thread).
    # Hotkey -> StateStore.update -> subscriber -> UI side-effect.
    # User-configurable bindings are loaded from disk; defaults apply if
    # the config is missing or corrupt.
    # ------------------------------------------------------------------
    from champ_assistant import hotkey_config as _hk_cfg
    from champ_assistant.hotkey_service import (
        DEFAULT_BINDINGS,
        HotkeyBinding,
        HotkeyService,
    )
    cfg = _hk_cfg.load()
    bindings: list[HotkeyBinding] = []
    for default in DEFAULT_BINDINGS:
        label = cfg.hotkeys.get(default.name, default.label)
        parsed = _hk_cfg.parse_combo(label)
        if parsed is None:
            # Configured combo is invalid → fall back to default.
            bindings.append(default)
            continue
        mods, vk = parsed
        bindings.append(HotkeyBinding(
            name=default.name, modifiers=mods, vk=vk, label=label,
        ))
        logger = logging.getLogger(__name__)
        logger.info("hotkey loaded from config: %s -> %s", default.name, label)
    hotkeys = HotkeyService(bindings=tuple(bindings))

    def _on_hotkey(name: str) -> None:
        cur = store.get()
        if name == "toggle_overlay":
            store.update(main_visible=not cur.main_visible)
        elif name == "toggle_lock":
            store.update(passthrough=not cur.passthrough)
        elif name in ("reset_positions", "reset_layout"):
            _reset_widget_positions()
        elif name == "toggle_scoreboard":
            # Manual flip — independent of vision detection. If the
            # vision service is also active, it may flip the value
            # back on the next frame; that's expected behavior (vision
            # = source of truth when the in-game scoreboard is open
            # for real, the hotkey is for cases where it's not).
            store.update(scoreboard_visible=not cur.scoreboard_visible)
        elif name == "toggle_insight":
            # Detail-view of the current top recommendation. Toggle
            # so a second press dismisses the panel. Latest top is
            # stashed on the panel itself by the LCDA dispatch loop.
            insight_panel.toggle(getattr(insight_panel, "_latest_top", None))
        elif name == "calibrate_minimap":
            # Manually re-position the minimap-timers overlay to match
            # the in-game minimap. Drag + resize while on; press again
            # to lock geometry and restore click-through.
            if minimap is not None:
                minimap.toggle_calibration()

    def _reset_widget_positions() -> None:
        from champ_assistant import layout as _layout
        from champ_assistant.ui.floating_widget import FloatingWidget
        # Wipe persisted layout (delete file) and snap each live widget
        # back to its DEFAULT_POS / DEFAULT_SIZE.
        _layout.store().reset()
        for widget in FloatingWidget._instances:
            x, y = widget.DEFAULT_POS
            w, h = widget.DEFAULT_SIZE
            widget.setGeometry(x, y, w, h)
            widget.show()  # un-hide if user had hidden it
        logging.getLogger(__name__).info("hotkey: reset all widget layouts")

    def _on_state_change(old, new) -> None:
        # main_visible: hide / show the main panel
        if old.main_visible != new.main_visible:
            if new.main_visible:
                overlay._switch_mode("champselect")
                overlay.show()
                overlay.raise_()
            else:
                overlay.hide()
        # passthrough: route mouse events to the game across all widgets
        if old.passthrough != new.passthrough:
            from champ_assistant.window_flags import set_passthrough
            from champ_assistant.ui.floating_widget import FloatingWidget
            set_passthrough(overlay._body, new.passthrough)
            for fw in FloatingWidget._instances:
                fw.set_passthrough(new.passthrough)

    store.subscribe(_on_state_change)
    from PyQt6.QtCore import Qt as _Qt
    hotkeys.hotkey_pressed.connect(_on_hotkey, _Qt.ConnectionType.QueuedConnection)
    if startup_mode.safe:
        # Global hotkey listener disabled in Safe Mode — Win32
        # RegisterHotKey + a daemon thread are exactly the kind of
        # OS-level resource that could be implicated in a crash loop.
        # User can still close the overlay window normally.
        logging.getLogger(__name__).info("hotkeys disabled (safe mode)")
    else:
        _safe_start("hotkeys", hotkeys.start)
    overlay._hotkeys = hotkeys  # keep alive
    lifecycle.register("hotkeys", hotkeys.stop)

    # Layout flush is registered last (= runs first in shutdown) so a
    # quick drag-then-quit doesn't lose the move to the 500ms debounce.
    def _flush_layout() -> None:
        from champ_assistant import layout as _layout
        _layout.store().flush_now()
    lifecycle.register("layout_flush", _flush_layout)
    # Qt loop stop runs *after* every other service has torn down so
    # late callbacks (hotkey signal, state listener) still find a live
    # event loop to dispatch into.
    lifecycle.register("qt_loop", loop.stop)

    # ------------------------------------------------------------------
    # Failure-recovery finalizers — run AFTER every service has stopped,
    # in registration order. session_summary first so its log line
    # captures the final counter values; clean_shutdown.marker last so
    # its presence definitively means "everything else completed OK".
    # ------------------------------------------------------------------
    from champ_assistant.session_summary import emit_session_summary as _emit_summary

    def _finalize_summary() -> None:
        _emit_summary(
            uptime_seconds=uptime_clock.elapsed(),
            diagnostics=diagnostics,
            scheduler=scheduler,
            telemetry_recorder=telemetry_recorder,
            state_store=store,
            safe_mode=startup_mode.safe,
        )

    def _finalize_clean_marker() -> None:
        _safe_mode.write_clean_shutdown_marker()

    lifecycle.register_finalizer("session_summary", _finalize_summary)
    lifecycle.register_finalizer("clean_shutdown_marker", _finalize_clean_marker)

    # Single shutdown entry: aboutToQuit → ordered teardown. Idempotent,
    # so a fallback finally: shutdown() during an exception path is safe.
    qt_app.aboutToQuit.connect(lifecycle.shutdown)

    try:
        with loop:
            loop.run_forever()
    finally:
        lifecycle.shutdown()
    return 0


async def _run_lcda_watcher(
    overlay: MainOverlay,
    floating_consumers: list[object],
    *,
    champions: dict | None = None,
    tags: object = None,
    cache_dir: Path | None = None,
    assistant: object = None,
) -> None:
    """Background task that polls LCDA and routes snapshots through the
    StateStore. The store's listeners then drive overlay + floating-widget
    repaints via the RenderScheduler.

    LCDA is only reachable while a match is loaded. The source already
    handles the alive/stale transition; we just commit each snapshot to
    the store and let pub/sub do the dispatch.

    ``champions``: DataDragon champions dict (int-id → Champion). Used to
    map LCDA display names to Meraki URL keys for build recommendations.
    ``tags``: TagsData, used to classify enemies for game-context scoring.
    ``cache_dir``: Meraki disk-cache directory. Build engine is disabled
    when None (e.g. dry-run or missing dir).
    """
    import time as _time

    from champ_assistant.lcda import LcdaClient, LcdaSource

    log = logging.getLogger(__name__)
    store = getattr(overlay, "_store", None)
    diagnostics = getattr(overlay, "_diagnostics", None)
    scheduler = getattr(overlay, "_scheduler", None)

    # Health monitor — track LCDA pipeline reliability (charter C2 + C5).
    # On consecutive store-commit failures the recovery callback clears
    # in-game state so the UI returns to idle; the next successful poll
    # naturally re-populates it. LcdaSource handles LCDA-unreachable
    # internally (backoff + retry); the callback only fires when the
    # state-commit layer itself is stuck.
    from champ_assistant import health_monitor as _health

    def _recover_lcda_pipeline() -> None:
        if store is not None:
            try:
                store.update(lcda_snapshot=None, phase="idle", game_time=0.0)
            except Exception:  # noqa: BLE001
                log.exception("lcda_recovery_state_reset_failed")

    _health.monitor().register_service(
        "lcda_pipeline", restart_callback=_recover_lcda_pipeline,
    )

    # Decision engine (charter B1) — runs once per snapshot, logs the
    # top recommendation AND pushes the full sorted list into the
    # floating recommendation panel (when present).
    from champ_assistant.advisor import decision_engine as _decisions
    from champ_assistant.ui.insight_panel import InsightPanel as _InsightPanelType
    from champ_assistant.ui.recommendation_panel import RecommendationPanel
    _last_recommendation: list[str] = [""]
    _last_game_result: list[str] = [""]   # track last known result to log on end
    _decision_log = logging.getLogger("champ_assistant.decisions")
    _rec_panel: RecommendationPanel | None = next(
        (w for w in floating_consumers if isinstance(w, RecommendationPanel)),
        None,
    )
    _insight: _InsightPanelType | None = next(
        (w for w in floating_consumers if isinstance(w, _InsightPanelType)),
        None,
    )

    # Build engine — fetches Meraki data on champion change and maintains a
    # BuildResult that evaluate() uses for situational item recommendations.
    # Disabled when cache_dir is None or the Meraki fetch fails.
    _build_result: list[object] = [None]        # mutable single-element wrapper
    _build_champion: list[str] = [""]           # last champion we built for
    _build_log = logging.getLogger("champ_assistant.build_engine")

    def _name_to_key_for(name: str) -> str:
        """DataDragon display name → Meraki URL key. Reads ``assistant.champions``
        at call time so the late-arriving DDragon prefetch is picked up — the
        closure-captured ``champions`` parameter was None at function-define
        time and stayed stale otherwise."""
        roster = getattr(assistant, "champions", None) or {}
        for c in roster.values():
            if c.name == name:
                return c.key
        return name.replace(" ", "")

    def _key_to_id(key: str) -> int:
        """DataDragon string key → numeric champion id (Sylas → 517).
        Same late-binding rationale as _name_to_key_for."""
        roster = getattr(assistant, "champions", None) or {}
        for c in roster.values():
            if c.key == key:
                return int(c.id)
        return 0

    async def _maybe_update_build(snap: object) -> None:
        """Compute (or reuse cached) build result for the current champion."""
        if cache_dir is None or snap is None:
            return
        # Identify the local player's champion from LCDA.
        active_summoner = str(getattr(snap, "active_summoner", "") or "")
        allies = list(getattr(snap, "allies", []) or [])
        local_champ_display = next(
            (
                str(getattr(p, "champion_name", "") or "")
                for p in allies
                if str(getattr(p, "summoner_name", "") or "") == active_summoner
            ),
            "",
        )
        if not local_champ_display or local_champ_display == _build_champion[0]:
            return  # same champion — reuse existing build result
        _build_champion[0] = local_champ_display
        _build_result[0] = None  # clear stale result immediately
        meraki_key = _name_to_key_for(local_champ_display)
        _build_log.info("build_engine_starting champion=%s key=%s", local_champ_display, meraki_key)
        try:
            from champ_assistant.advisor.build_engine import (
                GameContext,
                detect_archetype,
                recommend_items,
            )
            from champ_assistant.data.champion_scaling import extract_scaling_profile
            from champ_assistant.advisor.build_adapter import (
                SUSTAIN_KEYS,
                damage_profile_for_tags,
            )
            from champ_assistant.data.meraki import MerakiClient, MerakiError
            meraki_cache = cache_dir / "meraki"
            meraki_cache.mkdir(parents=True, exist_ok=True)
            async with MerakiClient(meraki_cache) as mc:
                champion_dict = await mc.fetch_champion(meraki_key)
                items_dict = await mc.fetch_items()
            # Build game context from current enemy team.
            enemies = list(getattr(snap, "allies", []) or [])  # recalculate after await
            enemies = list(getattr(snap, "enemies", []) or [])
            ap_count = ad_count = sustain_count = tank_count = 0
            for enemy in enemies:
                champ_name = str(getattr(enemy, "champion_name", "") or "")
                champ_tags = tags.tags_for(champ_name) if tags is not None else []
                profile = damage_profile_for_tags(champ_tags)
                if "AP" in profile:
                    ap_count += 1
                if "AD" in profile:
                    ad_count += 1
                if champ_name in SUSTAIN_KEYS:
                    sustain_count += 1
                if "Tank" in champ_tags:
                    tank_count += 1
            allies_val = sum(int(getattr(a, "items_value", 0) or 0) for a in (getattr(snap, "allies", []) or []))
            enemies_val = sum(int(getattr(e, "items_value", 0) or 0) for e in enemies)
            context = GameContext(
                enemy_ap_count=ap_count,
                enemy_ad_count=ad_count,
                enemy_sustain_count=sustain_count,
                enemy_tank_count=tank_count,
                game_time_s=float(getattr(snap, "game_time", 0.0) or 0.0),
                player_behind=(enemies_val - allies_val) > 3000,
            )
            archetype = detect_archetype(champion_dict)
            scaling = extract_scaling_profile(champion_dict)
            result = recommend_items(champion_dict, items_dict, archetype, context, scaling=scaling)
            _build_result[0] = result
            _build_log.info(
                "build_engine_done champion=%s play_style=%s core=%d situational=%d",
                local_champ_display, archetype.play_style,
                len(result.core_items), len(result.situational_items),
            )
            # Push the build as an LCU item set (game blueprint).
            # The LCU is only reachable when League is running; failures
            # here are non-fatal — the recommendation panel still shows.
            asyncio.ensure_future(_push_blueprint(meraki_key, result))
        except MerakiError as exc:
            _build_log.warning("build_engine_meraki_error champion=%s: %s", local_champ_display, exc)
        except Exception:
            _build_log.exception("build_engine_failed champion=%s", local_champ_display)

    async def _push_blueprint(champion_key: str, build_result: object) -> None:
        """Push BuildResult as an LCU item-set blueprint (fire-and-forget)."""
        from champ_assistant.lcu.client import LcuClient, LcuClientError
        from champ_assistant.lcu.item_sets import apply_item_set_from_result
        from champ_assistant.lcu.lockfile import (
            LockfileNotFound,
            find_lockfile,
            parse_lockfile,
        )
        try:
            lockfile_path = find_lockfile()
            lockfile = parse_lockfile(lockfile_path)
        except LockfileNotFound:
            _build_log.debug("blueprint_push_skipped: lockfile not found (client not running)")
            return
        champ_id = _key_to_id(champion_key)
        try:
            async with LcuClient(lockfile) as lcu:
                result = await apply_item_set_from_result(
                    lcu,
                    champion_key=champion_key,
                    champion_id=champ_id,
                    build_result=build_result,
                )
                if result is not None:
                    blocks = result.get("blocks") or []
                    total_items = sum(len(b.get("items") or []) for b in blocks)
                    _build_log.info(
                        "blueprint_pushed champion=%s blocks=%d items=%d",
                        champion_key, len(blocks), total_items,
                    )
                else:
                    _build_log.error(
                        "blueprint_push_empty champion=%s — build_item_set_from_result returned None",
                        champion_key,
                    )
        except LcuClientError as exc:
            _build_log.error("blueprint_push_failed champion=%s: %s", champion_key, exc)
        except Exception:
            _build_log.exception("blueprint_push_error champion=%s", champion_key)

    async def on_snapshot(snap: object) -> None:
        arrived = _time.monotonic()
        try:
            if store is not None:
                store.update(
                    lcda_snapshot=snap,
                    phase="in_game" if snap is not None else "idle",
                    last_lcda_received=arrived,
                    game_time=getattr(snap, "game_time", 0.0) if snap else 0.0,
                )
            # Existing widget surfaces stay live too — the store-listener
            # below routes the same snapshot to them via the scheduler.
        except Exception as exc:
            log.exception("lcda_state_commit_failed")
            _health.monitor().report_failure("lcda_pipeline", exc)
        else:
            # A snapshot landed cleanly (snap may be None when not in
            # game — that's still a successful poll, not a failure).
            _health.monitor().report_recovery("lcda_pipeline")
        if diagnostics is not None:
            diagnostics.record_event_latency_ms(
                (_time.monotonic() - arrived) * 1000.0
            )
        # Game-end detection: log result when LCDA transitions to None.
        result = getattr(snap, "game_result", "") or ""
        if result:
            _last_game_result[0] = result
        elif snap is None and _last_game_result[0]:
            log.info("game_ended result=%s", _last_game_result[0])
            _last_game_result[0] = ""
        # Decision engine pass — log when the top rec changes (so the
        # log doesn't flood) and push the full sorted list into the
        # floating panel for live on-screen display. Also keep the
        # InsightPanel's internal "latest top" up-to-date so the
        # Ctrl+Alt+I hotkey always opens with the current rec.
        # Update build engine if champion changed (debounced by champion name).
        try:
            await _maybe_update_build(snap)
        except Exception:
            log.exception("maybe_update_build_failed")
        try:
            recs = _decisions.evaluate(
                snap,
                spell_tracker=overlay.summoner_tracker.tracker(),
                situational_build=_build_result[0],
            )
            top = recs[0].text if recs else ""
            if top and top != _last_recommendation[0]:
                _last_recommendation[0] = top
                _decision_log.info("recommendation: %s", top)
            if _rec_panel is not None:
                _rec_panel.set_recommendations(recs)
            if _insight is not None:
                top_rec = recs[0] if recs else None
                # Stash for the hotkey path AND update the panel if
                # already open so it reflects the latest state live.
                _insight._latest_top = top_rec  # type: ignore[attr-defined]
                if _insight.isVisible():
                    _insight.set_recommendation(top_rec)
        except Exception:
            log.exception("decision_engine_failed")
        if scheduler is not None:
            scheduler.request_repaint()

    # Bridge the store back to the existing widget API: when the lcda
    # snapshot in state changes, dispatch to overlay + floating widgets.
    def _dispatch(old, new) -> None:  # type: ignore[no-untyped-def]
        if old.lcda_snapshot is new.lcda_snapshot:
            return
        try:
            overlay.update_lcda_snapshot(new.lcda_snapshot)  # type: ignore[arg-type]
        except Exception:
            log.exception("lcda_overlay_update_failed")
        for widget in floating_consumers:
            if not hasattr(widget, "update_snapshot"):
                continue
            try:
                widget.update_snapshot(new.lcda_snapshot)  # type: ignore[attr-defined]
            except Exception:
                log.exception("lcda_floating_widget_update_failed")

    if store is not None:
        store.subscribe(_dispatch)

    client = LcdaClient()
    source = LcdaSource(client, on_snapshot)
    try:
        await source.run()
    finally:
        source.close()
        await client.aclose()


async def _hydrate_champions_and_icons(
    overlay: MainOverlay, assistant: ChampAssistant, data_dir: Path
) -> None:
    """Fetch the full champion list + portraits from Data Dragon at startup.

    The hardcoded _STARTER_CHAMPIONS bootstrap dict only covers ~30 champs;
    real champ-select sessions reference any of ~170. Without this, enemies
    outside the bootstrap appear as "Champion #<id>" with no icon. We fetch
    the live roster (cached on disk for a week), update the orchestrator's
    champions table, then prefetch every icon in parallel.
    """
    from champ_assistant.data.datadragon import DataDragon

    log = logging.getLogger(__name__)
    cache_dir = data_dir.parent / "ddragon_cache"
    try:
        async with DataDragon(cache_dir) as dd:
            try:
                patch = await dd.fetch_latest_patch()
            except Exception:
                patch = "14.8.1"
            log.info("ddragon_patch=%s", patch)
            # Tie the runtime-counter cache to the actual current patch so
            # entries auto-invalidate at patch boundaries (and survive
            # indefinitely otherwise — matchups don't change between patches).
            if assistant._runtime_counters is not None:
                assistant._runtime_counters.set_patch(patch)

            try:
                champions = await dd.fetch_champions(patch)
                assistant.update_champions(champions)
                log.info("champions_loaded count=%d", len(champions))
            except Exception:
                # Network down + empty cache — bootstrap fallback. Counters
                # and tier lookups against the ~140 champions NOT in the
                # 30-entry starter list will silently miss. Surface the
                # degraded state so live users notice.
                log.exception(
                    "champions_fetch_failed — DEGRADED MODE: only %d champions "
                    "available (vs. ~170 in production). Counter / tier lookups "
                    "for missing champions will return empty.",
                    len(_STARTER_CHAMPIONS),
                )
                champions = {c.id: c for c in _STARTER_CHAMPIONS}
            # Post-hydration sanity: even successful fetches can return a
            # truncated list if DataDragon's CDN is misbehaving. Below 100
            # champions in 2026 = something is wrong.
            if len(champions) < 100:
                log.warning(
                    "champion_roster_suspicious count=%d (expected ~170) — "
                    "counter / tier lookups may be incomplete",
                    len(champions),
                )

            # Refresh tier list from Lolalytics (6 h TTL, non-blocking).
            try:
                from champ_assistant.data.refresh import maybe_refresh
                new_tiers = await maybe_refresh(data_dir, champions)
                if new_tiers is not None:
                    assistant.tiers = new_tiers
                    log.info("tier_refresh_applied")
            except Exception:
                log.exception("tier_refresh_error")

            # Attach Lolalytics counter fetcher (Tier 2.5 — free, fast).
            try:
                from champ_assistant.data.lolalytics_counters import (
                    LolalyticsCounterFetcher,
                )
                lolalytics_cache = cache_dir / "lolalytics_counters"
                lolalytics_cache.mkdir(parents=True, exist_ok=True)
                lol_fetcher = LolalyticsCounterFetcher(
                    lolalytics_cache, champions, patch=patch
                )
                if assistant._runtime_counters is not None:
                    assistant._runtime_counters.set_lolalytics(lol_fetcher)
                    log.info("lolalytics_counter_fetcher_attached")
            except Exception:
                log.exception("lolalytics_counter_fetcher_error")

            keys = sorted({c.key for c in champions.values()})
            log.info("icon_prefetch_start patch=%s keys=%d", patch, len(keys))
            icons_bytes = await dd.prefetch_icons(patch, keys)
            try:
                spell_bytes = await dd.prefetch_spell_icons(patch)
            except Exception:
                log.exception("spell_icons_fetch_failed")
                spell_bytes = {}
            # Item icons — same pattern, scoped to the items we know
            # how to map (ITEM_IDS contains every item we've curated
            # for builds.json). One-time prefetch per session; results
            # cache to disk via the DataDragon cache layer.
            try:
                from champ_assistant.data.items_data import ITEM_IDS
                item_ids = sorted(set(ITEM_IDS.values()))
                item_icon_bytes = await dd.prefetch_item_icons(patch, item_ids)
                log.info("item_icon_prefetch_done count=%d", len(item_icon_bytes))
            except Exception:
                log.exception("item_icons_fetch_failed")
                item_icon_bytes = {}
            # Rune icons — same prefetch pattern; PERK_IDS lists every
            # rune we might surface in a build display.
            try:
                from champ_assistant.data.perks_data import PERK_IDS
                perk_ids = sorted(set(PERK_IDS.values()))
                rune_icon_bytes = await dd.prefetch_rune_icons(patch, perk_ids)
                log.info("rune_icon_prefetch_done count=%d", len(rune_icon_bytes))
            except Exception:
                log.exception("rune_icons_fetch_failed")
                rune_icon_bytes = {}
    except Exception:
        log.exception("hydrate_failed")
        return

    # Convert PNG bytes → scaled QPixmap on the Qt thread.
    from PyQt6.QtCore import Qt as QtCore
    from PyQt6.QtGui import QPixmap

    pixmaps: dict[str, QPixmap] = {}
    for key, data in icons_bytes.items():
        pm = QPixmap()
        if not pm.loadFromData(data):
            continue
        pixmaps[key] = pm.scaled(
            32, 32,
            QtCore.AspectRatioMode.KeepAspectRatio,
            QtCore.TransformationMode.SmoothTransformation,
        )
    overlay.set_champion_icons(pixmaps)

    # The summoner tracker uses champion *names* (LCDA gives names, not keys).
    # Champions in the dict are keyed by Riot's string ID — match the LCDA
    # ``championName`` exactly by mapping name -> pixmap. For champions whose
    # display name and key differ (Wukong, Renata Glasc, etc.) Data Dragon's
    # ``name`` field is the source of truth.
    name_to_pixmap: dict[str, QPixmap] = {}
    for champ in champions.values():
        pm = pixmaps.get(champ.key)
        if pm is not None:
            name_to_pixmap[champ.name] = pm

    spell_pixmaps: dict[str, QPixmap] = {}
    for name, data in spell_bytes.items():
        pm = QPixmap()
        if not pm.loadFromData(data):
            continue
        spell_pixmaps[name] = pm.scaled(
            32, 32,
            QtCore.AspectRatioMode.KeepAspectRatio,
            QtCore.TransformationMode.SmoothTransformation,
        )
    overlay.summoner_tracker.set_champion_icons(name_to_pixmap)
    overlay.summoner_tracker.set_spell_icons(spell_pixmaps)
    # Per-lane scoreboard rows show champion icons + summoner-spell slots.
    # The ScoreboardWidget instance lives in the floating widget list — find
    # it via Qt's top-level widget registry rather than threading another arg
    # through this hydrate fn.
    from PyQt6.QtWidgets import QApplication
    from champ_assistant.ui.scoreboard_widget import ScoreboardWidget
    for w in QApplication.topLevelWidgets():
        if isinstance(w, ScoreboardWidget):
            w.set_champion_icons(name_to_pixmap)
            w.set_spell_icons(spell_pixmaps)

    # Item icons — keyed by item-NAME so PickCard's _build_line can
    # look them up directly from the build.items list (which carries
    # names, not IDs). Maps "Stridebreaker" → QPixmap.
    from champ_assistant.data.items_data import ITEM_IDS as _ITEM_IDS
    item_pixmaps: dict[str, QPixmap] = {}
    for item_name, item_id in _ITEM_IDS.items():
        data = item_icon_bytes.get(item_id)
        if data is None:
            continue
        pm = QPixmap()
        if not pm.loadFromData(data):
            continue
        item_pixmaps[item_name] = pm.scaled(
            32, 32,
            QtCore.AspectRatioMode.KeepAspectRatio,
            QtCore.TransformationMode.SmoothTransformation,
        )
    overlay.set_item_icons(item_pixmaps)

    # Rune icons — keyed by rune NAME so PickCard's rune row can look
    # them up directly off ChampionBuild.runes (which carries names).
    from champ_assistant.data.perks_data import PERK_IDS as _PERK_IDS
    rune_pixmaps: dict[str, QPixmap] = {}
    for rune_name, perk_id in _PERK_IDS.items():
        data = rune_icon_bytes.get(perk_id)
        if data is None:
            continue
        pm = QPixmap()
        if not pm.loadFromData(data):
            continue
        rune_pixmaps[rune_name] = pm.scaled(
            32, 32,
            QtCore.AspectRatioMode.KeepAspectRatio,
            QtCore.TransformationMode.SmoothTransformation,
        )
    overlay.set_rune_icons(rune_pixmaps)

    # Forward champion icons to the floating lobby widget if it's enabled.
    lobby = getattr(overlay, "_lobby_stats", None)
    if lobby is not None:
        lobby.set_champion_icons(pixmaps)
        # Re-render with the freshly arrived icons if a session was already
        # in flight when the prefetch finished.
        if getattr(overlay, "_last_view", None) is not None:
            lobby.update_view(overlay._last_view)

    log.info(
        "icon_prefetch_done champs=%d spells=%d items=%d runes=%d",
        len(pixmaps), len(spell_pixmaps), len(item_pixmaps), len(rune_pixmaps),
    )


async def _check_and_notify_update(
    overlay: MainOverlay,
    lifecycle: LifecycleManager,
) -> None:
    """One-shot startup check; if a newer release exists, surface it with an
    Install-now button. Clicking the button downloads, swaps, and relaunches.
    Also reports the previous update's outcome on first launch so silent
    failures (AV quarantine, robocopy issue) don't go unnoticed.

    Bails immediately if shutdown has begun — important when the user
    quits the app during the 5s startup window before the network probe
    has even returned.
    """
    from champ_assistant import __version__, update_snooze
    from champ_assistant.update_check import (
        check_for_update,
        install_dir,
        read_last_update_status,
    )

    if lifecycle.is_shutting_down:
        return

    # Surface the previous bat run's verdict if it failed. ``ok`` is
    # silent (success is the default expectation); ``stale`` is also
    # silent (probably from yesterday).
    status = read_last_update_status()
    if status is not None and status[0] == "fail":
        overlay.status_bar.set_info(
            f"Letzes Update fehlgeschlagen: {status[1][-80:]}",
            color="#FFB84A",
        )

    info = await check_for_update(__version__)
    if info is None or lifecycle.is_shutting_down:
        return
    log = logging.getLogger(__name__)

    # Honor the user's "Later" choice: same tag stays suppressed until
    # the snooze expires; a strictly-newer tag always surfaces.
    snooze = update_snooze.load()
    if snooze.is_active_for(info["tag"]):
        log.info("update_available tag=%s — snoozed until %.0f", info["tag"], snooze.until_ts)
        return
    log.info("update_available tag=%s url=%s", info["tag"], info["url"])

    target = install_dir()
    if target is None:
        # Dev mode (not frozen) — show the URL only, no install button.
        overlay.status_bar.set_info(
            f"Update: {info['tag']} verfügbar  —  {info['url']}",
            color="#4A9EFF",
        )
        return

    # Hold a strong reference to the launched task so it isn't GC'd mid-flight
    # (RUF006). Stored on the overlay since on_click outlives this function.
    overlay._update_task = None  # type: ignore[attr-defined]
    overlay._update_in_progress = False  # type: ignore[attr-defined]

    def on_click() -> None:
        # Dedup guard: clicking "Install now" twice while the first
        # download is still streaming would spawn two parallel apply_update
        # tasks, two sidecar bats, and a race for the install dir.
        if getattr(overlay, "_update_in_progress", False):
            log.info("update click ignored — already in progress")
            return
        if lifecycle.is_shutting_down:
            log.info("update click ignored — app is shutting down")
            return
        # Clear any previous snooze — the user explicitly chose to install,
        # so a stale snooze for the same tag must not block a retry path.
        update_snooze.clear()
        overlay._update_in_progress = True  # type: ignore[attr-defined]
        overlay._update_task = asyncio.ensure_future(  # type: ignore[attr-defined]
            _run_update(overlay, info["tag"], target, lifecycle)
        )

    def on_snooze() -> None:
        update_snooze.snooze_tag(info["tag"])
        overlay.status_bar.dismiss_update()
        log.info("update_snoozed tag=%s", info["tag"])

    overlay.status_bar.show_update_available(info["tag"], on_click, on_snooze)


async def _run_update(
    overlay: MainOverlay,
    tag: str,
    target: Path,
    lifecycle: LifecycleManager,
) -> None:
    """Download + extract + spawn sidecar + quit the app.

    Aborts cleanly if the app starts shutting down mid-download — the
    sidecar bat is never written, so a partial download in the staging
    directory is harmless and gets cleaned up on the next run.
    """
    from champ_assistant.update_check import apply_update

    log = logging.getLogger(__name__)
    bar = overlay.status_bar
    try:
        if lifecycle.is_shutting_down:
            log.info("update aborted before start — shutdown in progress")
            return
        await apply_update(
            tag,
            install_directory=target,
            progress=bar.set_update_progress,
        )
        if lifecycle.is_shutting_down:
            log.info("update finished but app is shutting down — sidecar may not run")
            return
    except asyncio.CancelledError:
        log.info("update cancelled — task was torn down")
        raise
    except Exception as exc:
        log.exception("update_failed")
        bar.update_failed(f"Update fehlgeschlagen: {exc}")
        return
    finally:
        overlay._update_in_progress = False  # type: ignore[attr-defined]
    bar.set_update_progress("App startet neu…")
    log.info("update_applied tag=%s — quitting to let sidecar swap files", tag)
    QApplication.quit()


async def _run_headless(args: argparse.Namespace) -> int:
    """No-UI mode: print events + summarized session views to stdout."""
    import json

    overlay = MainOverlay()  # built but never shown
    assistant = _build_assistant(args, overlay)

    def on_view(view) -> None:  # type: ignore[no-untyped-def]
        out = {
            "state": view.connection_state,
            "phase": view.session.phase if view.session else None,
            "suggestions": [
                {"key": s.champion_key, "score": round(s.score, 1), "tier": s.tier}
                for s in view.suggestions[:3]
            ],
        }
        print(json.dumps(out), flush=True)

    assistant._view_callback = on_view  # type: ignore[attr-defined]

    try:
        await assistant.run()
    except asyncio.CancelledError:
        pass
    return 0


def _log_directory() -> Path:
    """Per-platform log directory."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant" / "logs"
    return Path.home() / ".champ-assistant" / "logs"


def _setup_file_logger(level: int = logging.INFO) -> Path:
    """Add a rotating file handler so we have visibility in the frozen exe.

    The exe is built with console=False (no stdout/stderr in a windowed app),
    so without file logging there's no way to diagnose runtime issues. We
    keep the handler at INFO and silence the chattiest third-party loggers
    (httpcore + qasync emit thousands of DEBUG lines per second on Windows
    IOCP and drown out the app's own logs).
    """
    log_dir = _log_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    handler = logging.handlers.RotatingFileHandler(
        str(log_file),
        maxBytes=5_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(make_formatter())
    install_tag_filter(handler)
    handler.setLevel(level)

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)

    # Silence noisy third-party loggers — they fire a debug line per byte
    # of every HTTP body (icon prefetch alone produces ~30 MB of log noise).
    for noisy in ("httpcore", "httpx", "qasync", "asyncio",
                  "PIL", "diskcache", "websockets", "websockets.client",
                  "websockets.server"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def _load_dotenv_files() -> None:
    """Load .env from the bundle directory and CWD so users can drop their
    GROQ_API_KEY into a file next to the exe instead of setting a system
    environment variable. Failures are swallowed — .env is optional."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / ".env")
    candidates.append(Path.cwd() / ".env")
    for p in candidates:
        if p.is_file():
            try:
                load_dotenv(p, override=False)
            except Exception:
                pass


def _bootstrap_install(
    staged_dir: Path,
    install_dir: Path,
    *,
    parent_pid: int,
) -> int:
    """Minimal no-UI mode: wait for old app to exit, copy files, relaunch.

    Runs when the new exe is launched from the staging directory with
    --bootstrap-install. Does not import Qt. Writes diagnostics to the
    standard update log so the next normal start can surface failures.
    """
    import ctypes
    import shutil
    import time

    if sys.platform.startswith("win"):
        localappdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        log_path = Path(localappdata) / "ChampAssistant" / "logs" / "last-update.log"
    else:
        log_path = Path.home() / ".champ-assistant" / "logs" / "last-update.log"

    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(msg: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"[bootstrap] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass

    # Overwrite any stale log from a previous run.
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass

    _log(f"start. staged={staged_dir} install={install_dir} parent_pid={parent_pid}")

    # --- Wait for old app to exit ---
    if parent_pid and sys.platform.startswith("win"):
        SYNCHRONIZE = 0x00100000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if handle:
            kernel32.WaitForSingleObject(handle, 30_000)  # max 30 s
            kernel32.CloseHandle(handle)
            _log("parent exited (WaitForSingleObject)")
        else:
            _log("parent_pid not found — already exited")
    elif parent_pid:
        import subprocess as _sp
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                _sp.run(["kill", "-0", str(parent_pid)], capture_output=True, check=True)
            except Exception:
                break
            time.sleep(0.5)
        _log("parent gone (poll)")

    time.sleep(1)  # extra margin for file handle release

    # --- Copy staged_dir → install_dir ---
    # shutil.copytree with dirs_exist_ok overwrites existing files and adds new
    # ones. Reading from the running staged exe is allowed (Windows grants shared
    # read access to running executables). Writing to install_dir is fine because
    # the old app has exited.
    _log(f"copying {staged_dir} → {install_dir}")
    for attempt in range(1, 4):
        try:
            shutil.copytree(str(staged_dir), str(install_dir), dirs_exist_ok=True)
            _log("copy OK")
            break
        except OSError as exc:
            _log(f"copy attempt {attempt} failed: {exc}")
            if attempt < 3:
                time.sleep(2)
            else:
                _log("FAIL: could not copy files after 3 attempts")
                return 1

    # --- Launch from install_dir ---
    from champ_assistant.update_check import EXE_NAME
    new_exe = install_dir / EXE_NAME
    if not new_exe.is_file():
        _log(f"FAIL: {new_exe} not found after copy")
        return 1

    try:
        import subprocess as _sp
        _sp.Popen([str(new_exe)], cwd=str(install_dir))
        _log(f"SUCCESS: launched {new_exe}")
    except OSError as exc:
        _log(f"FAIL: could not launch {new_exe}: {exc}")
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Bootstrap-install path: launched by the previous version's apply_update
    # to install ourselves. Handled before any heavy imports (no Qt loaded).
    if args.bootstrap_install:
        staged = Path(args.bootstrap_staged) if args.bootstrap_staged else Path(sys.executable).resolve().parent
        return _bootstrap_install(
            staged,
            Path(args.bootstrap_install),
            parent_pid=args.bootstrap_parent_pid or 0,
        )

    _load_dotenv_files()

    # Install tagged formatter on the default stderr handler. We can't
    # rely on logging.basicConfig's `format=` because the stamped
    # ``subsystem`` field comes from a per-handler Filter, not the
    # formatter alone — so we configure the root + stderr handler
    # explicitly here.
    root = logging.getLogger()
    root.setLevel(args.log_level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stderr = logging.StreamHandler()
        stderr.setFormatter(make_formatter())
        install_tag_filter(stderr)
        root.addHandler(stderr)
    else:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler):
                h.setFormatter(make_formatter())
                install_tag_filter(h)
    log_file = _setup_file_logger()
    logging.getLogger(__name__).info("startup log_file=%s args=%r", log_file, vars(args))

    if not args.dry_run and not args.no_ui:
        # Live mode (LCU) without UI doesn't make sense for users; allow with --no-ui.
        print("[champ-assistant] live mode runs the same way as --dry-run, "
              "minus the fixture replay. Phase 7 wires DataDragon for champion names.",
              file=sys.stderr)

    if args.no_ui:
        # Headless still constructs MainOverlay (orchestrator wiring is shared),
        # and any QWidget requires a live QApplication. PyQt6 doesn't keep it
        # alive on its own — the Python local reference does — so we MUST bind
        # it here for the duration of asyncio.run().
        qt_app = QApplication.instance() or QApplication(sys.argv[:1])
        try:
            return asyncio.run(_run_headless(args))
        except KeyboardInterrupt:
            return 0
        finally:
            del qt_app

    return _run_with_ui(args)


if __name__ == "__main__":
    sys.exit(main())
