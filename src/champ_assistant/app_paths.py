"""Resolved app directories — single source of truth for log / cache /
state / resource paths.

Replaces three pre-existing duplicates that all computed the same things
slightly differently:

* ``performance_monitor._log_dir`` — Windows ``%LOCALAPPDATA%/ChampAssistant``
  vs Unix ``~/.champ-assistant`` log directory
* ``__main__._resource_root`` — PyInstaller-aware bundle/repo root
* ``app._dump_failed_payload`` — discovered the log directory by iterating
  ``logging.getLogger().handlers`` and reading ``baseFilename``. That last
  one is brittle: if a handler reorganization replaces the rotating file
  handler with (e.g.) a JSON handler, the dump silently goes nowhere
  because the function swallows the exception.

The two pre-existing functions stay in place as thin delegators so any
existing tests that ``monkeypatch`` them keep working. New code should
import from this module directly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _user_data_root() -> Path:
    """Top-level directory for user-private app state — logs, layout
    persistence, runtime caches, etc.

    Cross-platform: ``%LOCALAPPDATA%/ChampAssistant`` on Windows,
    ``~/.champ-assistant`` elsewhere.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def log_dir() -> Path:
    """Directory for log files (``app.log``, ``performance.log``,
    ``rule_timing.log``, ``failed_payload_*.json``).

    Caller is responsible for ``mkdir(parents=True, exist_ok=True)`` when
    actually writing — this module is path-resolution only, no I/O.
    """
    return _user_data_root() / "logs"


def state_dir() -> Path:
    """Directory for persisted UI state (layout positions, snooze flags,
    overlay config). Same parent as logs."""
    return _user_data_root()


def resource_root() -> Path:
    """Repo root in dev, bundle root in a PyInstaller frozen exe.

    PyInstaller sets ``sys.frozen`` and exposes the unpacked bundle path
    via ``sys._MEIPASS``. From source, fall back to the repo root inferred
    from this file's location.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]
