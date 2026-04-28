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
from pathlib import Path

import qasync
from collections.abc import Callable
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


def _resource_root() -> Path:
    """Repo root in dev, bundle root in a PyInstaller frozen exe.

    PyInstaller sets ``sys.frozen`` and exposes the unpacked bundle path via
    ``sys._MEIPASS``. From source, fall back to the repo root inferred from
    this file's location.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


DEFAULT_FIXTURE_DIR = _resource_root() / "tests" / "fixtures" / "sessions"
DEFAULT_DATA_DIR = _resource_root() / "data"


# Hardcoded starter dict for Phase 6. Phase 7 replaces this with a live
# DataDragon fetch (cached on disk). Numeric IDs are Riot's official ones.
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
    parser.add_argument("--no-ui", action="store_true",
                        help="Skip the Qt window (print events instead).")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                        help=f"Static data directory (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
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
    except Exception:  # noqa: BLE001 — subsystem must not crash the app
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
    lifecycle = LifecycleManager()

    _enable_gpu_backend()
    qt_app = QApplication(sys.argv[:1])

    overlay = MainOverlay(load_persisted_state=True)
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
    scheduler = RenderScheduler()
    diagnostics = Diagnostics()
    lifecycle.register("scheduler", scheduler.stop)
    lifecycle.register("diagnostics", diagnostics.stop)
    diagnostics.attach_scheduler(scheduler)
    diagnostics.attach_store(store)
    overlay._store = store              # type: ignore[attr-defined]
    overlay._scheduler = scheduler      # type: ignore[attr-defined]
    overlay._diagnostics = diagnostics  # type: ignore[attr-defined]
    # Drive the embedded power-spike panel's fade animation off the
    # central tick instead of its own QTimer (P5).
    overlay.power_spike_panel.connect_scheduler(scheduler)

    assistant = _build_assistant(args, overlay)
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

    # When the user saves a new Riot API key in Settings, rebuild the
    # profile service so subsequent enemy profile lookups use it.
    def _on_settings_changed() -> None:
        assistant._profile_service = _build_profile_service()
        assistant._enemy_profiles_by_cell.clear()
        if assistant._latest_session is not None:
            assistant._push_view(assistant._build_view(assistant._latest_session))
    overlay.settings_changed.connect(_on_settings_changed)

    overlay.show()

    crash = CrashHandler()
    # Surface any swallowed exception in the info slot so silent failures
    # (orchestrator dying, source raising during init, etc.) stay visible
    # instead of being overwritten by the next connection-state refresh.
    def _on_crash(msg: str) -> None:
        overlay.status_bar.set_info(f"Error: {msg[:80]}", color="#FF6B6B")
    crash.subscribe(_on_crash)

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
    from champ_assistant import overlay_config as _ovc
    from champ_assistant.ui.lobby_stats_widget import LobbyStatsWidget
    from champ_assistant.ui.minimap_timers_widget import MinimapTimersWidget
    from champ_assistant.ui.scoreboard_widget import ScoreboardWidget
    persisted = _ovc.load()
    floating: list[object] = []
    if persisted.show_scoreboard:
        scoreboard = ScoreboardWidget()
        floating.append(scoreboard)
    if persisted.show_minimap_timers:
        minimap = MinimapTimersWidget()
        minimap.connect_scheduler(scheduler)
        floating.append(minimap)
    lobby_stats: LobbyStatsWidget | None = None
    if persisted.show_lobby_stats:
        lobby_stats = LobbyStatsWidget()
        # Hooked into the SessionView pipeline below (not LCDA).
        overlay._lobby_stats = lobby_stats  # type: ignore[attr-defined]

    lcda_task = loop.create_task(
        _run_lcda_watcher(overlay, floating), name="lcda-watcher"
    )
    lifecycle.register("lcda_task", lcda_task.cancel)

    # Start production-grade infrastructure now that the loop is set up.
    # Each start is isolated — a misbehaving subsystem must not block the
    # rest of init (subsystem isolation, P2).
    _safe_start("scheduler", scheduler.start)
    _safe_start("diagnostics", diagnostics.start)

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
) -> None:
    """Background task that polls LCDA and routes snapshots through the
    StateStore. The store's listeners then drive overlay + floating-widget
    repaints via the RenderScheduler.

    LCDA is only reachable while a match is loaded. The source already
    handles the alive/stale transition; we just commit each snapshot to
    the store and let pub/sub do the dispatch.
    """
    import time as _time

    from champ_assistant.lcda import LcdaClient, LcdaSource

    log = logging.getLogger(__name__)
    store = getattr(overlay, "_store", None)
    diagnostics = getattr(overlay, "_diagnostics", None)
    scheduler = getattr(overlay, "_scheduler", None)

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
        except Exception:
            log.exception("lcda_state_commit_failed")
        if diagnostics is not None:
            diagnostics.record_event_latency_ms(
                (_time.monotonic() - arrived) * 1000.0
            )
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
                log.exception("champions_fetch_failed")
                champions = {c.id: c for c in _STARTER_CHAMPIONS}

            keys = sorted({c.key for c in champions.values()})
            log.info("icon_prefetch_start patch=%s keys=%d", patch, len(keys))
            icons_bytes = await dd.prefetch_icons(patch, keys)
            try:
                spell_bytes = await dd.prefetch_spell_icons(patch)
            except Exception:
                log.exception("spell_icons_fetch_failed")
                spell_bytes = {}
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

    # Forward champion icons to the floating lobby widget if it's enabled.
    lobby = getattr(overlay, "_lobby_stats", None)
    if lobby is not None:
        lobby.set_champion_icons(pixmaps)
        # Re-render with the freshly arrived icons if a session was already
        # in flight when the prefetch finished.
        if getattr(overlay, "_last_view", None) is not None:
            lobby.update_view(overlay._last_view)

    log.info(
        "icon_prefetch_done champs=%d spells=%d",
        len(pixmaps), len(spell_pixmaps),
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
    from champ_assistant import __version__
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
        overlay._update_in_progress = True  # type: ignore[attr-defined]
        overlay._update_task = asyncio.ensure_future(  # type: ignore[attr-defined]
            _run_update(overlay, info["tag"], target, lifecycle)
        )

    overlay.status_bar.show_update_available(info["tag"], on_click)


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
