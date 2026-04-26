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
from PyQt6.QtWidgets import QApplication

# Absolute imports (not relative) so PyInstaller can run this file directly
# as the entry point. PyInstaller executes __main__.py without setting
# __package__, which would break `from .app import ...`. Absolute imports
# work in both modes — `python -m champ_assistant` AND a frozen exe.
from champ_assistant.app import ChampAssistant
from champ_assistant.data.loader import load_counters, load_tags, load_tiers
from champ_assistant.data.models import Champion
from champ_assistant.lcu.sources import FixtureLcuSource, LcuSource, RealLcuSource
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
    return ChampAssistant(
        source=_make_source(args),
        overlay=overlay,
        counters=load_counters(args.data_dir / "counters.json"),
        tiers=load_tiers(args.data_dir / "tiers.json"),
        tags=load_tags(args.data_dir / "tags.json"),
        champions=_starter_champion_index(),
    )


def _run_with_ui(args: argparse.Namespace) -> int:
    qt_app = QApplication(sys.argv[:1])

    overlay = MainOverlay()
    assistant = _build_assistant(args, overlay)
    overlay.show()

    crash = CrashHandler()
    # Surface any swallowed exception in the status bar so silent failures
    # (orchestrator dying, source raising during init, etc.) are visible
    # instead of leaving the UI stuck on "Disconnected" forever.
    def _on_crash(msg: str) -> None:
        bar = overlay.status_bar
        bar._label.setText(f"Error: {msg[:80]}")
        bar._label.setStyleSheet("color: #FF6B6B; padding: 0 8px;")
    crash.subscribe(_on_crash)

    loop = qasync.QEventLoop(qt_app)
    asyncio.set_event_loop(loop)
    crash.install(loop=loop)
    qt_app.aboutToQuit.connect(loop.stop)

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
        _check_and_notify_update(overlay), name="update-check"
    )

    try:
        with loop:
            loop.run_forever()
    finally:
        for t in (consumer, icon_task, update_task):
            t.cancel()
        crash.uninstall()
    return 0


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
            except Exception:  # noqa: BLE001
                patch = "14.8.1"
            log.info("ddragon_patch=%s", patch)

            try:
                champions = await dd.fetch_champions(patch)
                assistant.update_champions(champions)
                log.info("champions_loaded count=%d", len(champions))
            except Exception:  # noqa: BLE001
                log.exception("champions_fetch_failed")
                champions = {c.id: c for c in _STARTER_CHAMPIONS}

            keys = sorted({c.key for c in champions.values()})
            log.info("icon_prefetch_start patch=%s keys=%d", patch, len(keys))
            icons_bytes = await dd.prefetch_icons(patch, keys)
    except Exception:  # noqa: BLE001
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
    log.info("icon_prefetch_done loaded=%d", len(pixmaps))


async def _check_and_notify_update(overlay: MainOverlay) -> None:
    """One-shot startup check; if a newer release exists, surface it in the status bar."""
    from champ_assistant import __version__
    from champ_assistant.update_check import check_for_update

    info = await check_for_update(__version__)
    if info is None:
        return
    bar = overlay.status_bar
    base_text = f"Update: {info['tag']} verfügbar — {info['url']}"
    bar._label.setText(base_text)
    bar._label.setStyleSheet("color: #4A9EFF; padding: 0 8px;")
    logging.getLogger(__name__).info("update_available tag=%s url=%s", info["tag"], info["url"])


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


def _setup_file_logger(level: int = logging.DEBUG) -> Path:
    """Add a rotating file handler so we have visibility in the frozen exe.

    The exe is built with console=False (no stdout/stderr in a windowed app),
    so without file logging there's no way to diagnose runtime issues. Every
    LCU call, WS event, and orchestrator state transition lands here.
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
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.setLevel(level)

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)
    return log_file


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
