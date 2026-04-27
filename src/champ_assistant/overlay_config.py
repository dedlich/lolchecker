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
    width: int = 320
    height: int = 720
    anchor: str = "right"  # right | left | none
    always_on_top: bool = True
    frameless: bool = True
    collapsed: bool = False  # user-toggled "minimize" state


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
                  "always_on_top", "frameless", "collapsed"):
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
