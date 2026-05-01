"""Persisted overlay window state (position, size, anchor).

The frozen exe runs with no console — users can't pass CLI flags every
launch. Persisting their last window placement keeps the overlay where
they parked it. Stored as JSON next to the app's log files in:

  %LOCALAPPDATA%\\ChampAssistant\\overlay.json   (Windows)
  ~/.champ-assistant/overlay.json                (everywhere else)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class OverlayState:
    x: int | None = None
    y: int | None = None
    width: int = 640         # champ-select-friendly default
    height: int = 720
    anchor: str = "right"  # right | left | none
    always_on_top: bool = False  # turned on automatically only in-game
    frameless: bool = True
    collapsed: bool = False  # user-toggled "minimize" state
    opacity: float = 0.92    # only applied in overlay mode
    show_objectives: bool = True
    show_summoners: bool = True
    show_spikes: bool = True
    show_scoreboard: bool = True
    show_minimap_timers: bool = True
    show_lobby_stats: bool = True
    floating_positions: dict | None = None  # widget-key -> [x, y]
    # First-launch onboarding banner — flips to True the first time the
    # user dismisses it (or skips). Default False = show on next start.
    onboarding_seen: bool = False
    # Diagnostics logging (CPU/mem/FPS every 10s). Default on so existing
    # users keep their behavior; toggleable via Settings → Diagnostics.
    diagnostics_enabled: bool = True
    # Vision-based automatic camp detection (minimap color heuristic).
    # Enabled by default — arms jungle timers automatically when camp
    # icons disappear from the minimap, same as Blitz/Porofessor.
    enable_auto_camp_detection: bool = True
    # Vision-based scoreboard visibility detection — drives the
    # scoreboard-scoped gold-diff overlay (panel only renders while
    # the in-game tab-scoreboard is up). Default ON so users see the
    # gold-diff feature without manually enabling it; gracefully
    # no-ops on non-Windows or in safe mode.
    enable_scoreboard_detection: bool = True
    # Update notifications via GitHub Releases. On by default.
    # Toggleable so users on metered connections / privacy-sensitive
    # setups can opt out.
    enable_update_check: bool = True
    # Telemetry recording — local-only, append-only JSONL. On by default.
    # Toggleable so users who don't want disk writes can opt out without
    # entering Safe Mode.
    enable_telemetry: bool = True
    # Low Resource Mode (Strategy A5) — single master switch that
    # forces every optional subsystem off + reduces render rate. Used
    # for low-end laptops or when the user is running a stream encoder
    # that needs every spare CPU cycle. The other per-feature flags
    # stay as the user set them; LRM overrides at startup, so toggling
    # LRM off again restores the prior preferences.
    low_resource_mode: bool = False
    # Focus Mode (v2 spec) — collapses the recommendation panel to
    # the top-1 alert only. ON by default per the "show one decision
    # at a time" UX principle from the v2 spec; users who want the
    # top-3 context fan-out can disable it in Settings.
    focus_mode: bool = True


def _config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def config_path() -> Path:
    return _config_dir() / "overlay.json"


def load() -> OverlayState:
    """Read the persisted state; return defaults on any error."""
    path = config_path()
    if not path.is_file():
        return OverlayState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.info("overlay_config_unreadable: %s", exc)
        return OverlayState()
    if not isinstance(data, dict):
        return OverlayState()
    state = OverlayState()
    for field in ("x", "y", "width", "height", "anchor",
                  "always_on_top", "frameless", "collapsed",
                  "opacity", "show_objectives", "show_summoners",
                  "show_spikes", "show_scoreboard", "show_minimap_timers",
                  "show_lobby_stats", "floating_positions",
                  "onboarding_seen", "diagnostics_enabled",
                  "enable_auto_camp_detection",
                  "enable_scoreboard_detection",
                  "enable_update_check",
                  "enable_telemetry",
                  "low_resource_mode",
                  "focus_mode"):
        if field in data:
            setattr(state, field, data[field])
    return state


def save(state: OverlayState) -> None:
    """Write state to disk; failures are logged, never raised."""
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("overlay_config_save_failed: %s", exc)
