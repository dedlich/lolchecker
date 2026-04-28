"""Persistent widget layout with multi-monitor safety + debounced save.

Storage: ``%LOCALAPPDATA%/ChampAssistant/layout.json`` on Windows,
``~/.champ-assistant/layout.json`` everywhere else. Schema:

  {
    "widgets": {
      "minimap_timers": {
        "x": 1420, "y": 780,
        "visible": true,
        "monitor_id": "\\\\.\\DISPLAY2"
      },
      ...
    }
  }

Save flow:
  widget drag end / visibility flip
    → LayoutStore.mark(key, WidgetLayout(...))
    → 500 ms debounce timer (re)started
    → on timeout: write JSON to disk

Restore flow:
  LayoutStore() reads disk on construction
  FloatingWidget._load_position calls layout.safe_position_for() which
    looks up the saved monitor by device name; if it's gone, falls back
    to the primary screen, then clamps the (x, y) into the available
    geometry so the widget can never end up off-screen.

Migration: on first run a brand-new layout.json doesn't exist; in that
case we copy the legacy ``floating_positions`` field from
``overlay.json`` so existing users don't lose their layouts.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QGuiApplication

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class WidgetLayout:
    x: int
    y: int
    visible: bool = True
    monitor_id: str = ""  # QScreen.name(), e.g. "\\\\.\\DISPLAY1"


def _layout_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def layout_path() -> Path:
    return _layout_dir() / "layout.json"


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class LayoutStore(QObject):
    """In-memory mirror of the on-disk layout, with a debounced save."""

    DEBOUNCE_MS = 500

    def __init__(self) -> None:
        super().__init__()
        self._layouts: dict[str, WidgetLayout] = {}
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEBOUNCE_MS)
        self._timer.timeout.connect(self._flush)
        self._load_from_disk()

    # -- read --------------------------------------------------------------

    def get(self, key: str) -> WidgetLayout | None:
        return self._layouts.get(key)

    def all_layouts(self) -> dict[str, WidgetLayout]:
        return dict(self._layouts)

    # -- write -------------------------------------------------------------

    def mark(self, key: str, layout: WidgetLayout) -> None:
        """Record a layout change. No-op if identical to the current entry.
        Debounced — disk write happens 500 ms after the last call."""
        if self._layouts.get(key) == layout:
            return
        self._layouts[key] = layout
        self._dirty = True
        self._timer.start()  # restart debounce window

    def reset(self) -> None:
        """Wipe the in-memory layouts and delete the on-disk file.
        Used by the Ctrl+Alt+D hotkey."""
        self._layouts.clear()
        self._dirty = False
        self._timer.stop()
        try:
            layout_path().unlink()
            logger.info("layout reset to defaults")
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("layout reset: file delete failed: %s", exc)

    def flush_now(self) -> None:
        """Force an immediate write — called from app shutdown so a
        pending debounce timer doesn't drop unsaved state."""
        if not self._dirty:
            return
        self._timer.stop()
        self._flush()

    # -- internals ---------------------------------------------------------

    def _flush(self) -> None:
        path = layout_path()
        payload = {
            "widgets": {
                key: {
                    "x": v.x, "y": v.y,
                    "visible": v.visible,
                    "monitor_id": v.monitor_id,
                }
                for key, v in self._layouts.items()
            }
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            logger.debug("layout saved: %d widgets", len(self._layouts))
            self._dirty = False
        except OSError as exc:
            logger.warning("layout save failed: %s", exc)

    def _load_from_disk(self) -> None:
        path = layout_path()
        if not path.is_file():
            self._migrate_from_overlay_config()
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("layout corrupt: %s — using defaults", exc)
            return
        if not isinstance(data, dict):
            logger.warning("layout corrupt: not an object — using defaults")
            return
        widgets = data.get("widgets") or {}
        if not isinstance(widgets, dict):
            logger.warning("layout corrupt: widgets not an object — using defaults")
            return
        for key, raw in widgets.items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                continue
            try:
                self._layouts[key] = WidgetLayout(
                    x=int(raw["x"]),
                    y=int(raw["y"]),
                    visible=bool(raw.get("visible", True)),
                    monitor_id=str(raw.get("monitor_id", "")),
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.warning("layout entry %r unreadable: %s", key, exc)
        logger.info("layout loaded: %d widgets restored", len(self._layouts))

    def _migrate_from_overlay_config(self) -> None:
        """First-run migration from v0.11.x's overlay.json.floating_positions.
        Pulls the [x, y] tuples into our richer WidgetLayout shape so
        existing users keep their parked positions across the upgrade."""
        try:
            from . import overlay_config
            state = overlay_config.load()
        except Exception:  # noqa: BLE001 — best effort
            return
        positions = state.floating_positions or {}
        if not positions:
            return
        for key, pos in positions.items():
            if not (isinstance(pos, list) and len(pos) >= 2):
                continue
            try:
                self._layouts[key] = WidgetLayout(
                    x=int(pos[0]), y=int(pos[1]),
                    visible=True, monitor_id="",
                )
            except (TypeError, ValueError):
                continue
        if self._layouts:
            logger.info(
                "layout migrated %d widgets from overlay_config",
                len(self._layouts),
            )
            self._dirty = True
            self._timer.start()


# --------------------------------------------------------------------------
# Restore helpers
# --------------------------------------------------------------------------
def safe_position_for(
    saved: WidgetLayout | None,
    *,
    fallback_pos: tuple[int, int],
    fallback_size: tuple[int, int],
    widget_key: str = "widget",
) -> tuple[int, int]:
    """Compute a screen-safe (x, y) for a widget's saved layout.

    Resolution / monitor-change resilience:
      1. If saved.monitor_id is gone, fall back to the primary screen
         (and log a warning).
      2. If the saved (x, y) lies outside the chosen screen's available
         geometry, clamp into bounds (and log).

    Returns ``fallback_pos`` only when there is *no* saved layout AND
    no usable screen — the truly degenerate case.
    """
    if saved is None:
        return fallback_pos

    screens = QGuiApplication.screens()
    target = None
    if saved.monitor_id:
        target = next((s for s in screens if s.name() == saved.monitor_id), None)
        if target is None:
            logger.warning(
                "layout corrected: %s saved monitor %r missing — using primary",
                widget_key, saved.monitor_id,
            )
    if target is None:
        target = QGuiApplication.primaryScreen()
    if target is None:
        return fallback_pos

    geo = target.availableGeometry()
    w, h = fallback_size
    # Qt's QRect.right()/bottom() are inclusive (x + width - 1), so the
    # rightmost top-left x that keeps the widget fully on-screen is
    # x + width - w, equivalently right() + 1 - w.
    max_x = geo.x() + geo.width() - w
    max_y = geo.y() + geo.height() - h
    x = max(geo.left(), min(max_x, saved.x))
    y = max(geo.top(),  min(max_y, saved.y))
    if (x, y) != (saved.x, saved.y):
        logger.warning(
            "layout corrected: %s moved into visible bounds (%d,%d) -> (%d,%d)",
            widget_key, saved.x, saved.y, x, y,
        )
    return x, y


def current_monitor_id(widget) -> str:  # type: ignore[no-untyped-def]
    """Return the device name of the screen currently containing
    ``widget`` (e.g. ``\\\\.\\DISPLAY1`` on Windows). Empty string when
    the widget hasn't been shown yet or no screens exist."""
    handle = widget.windowHandle()
    screen = handle.screen() if handle is not None else None
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    return screen.name() if screen is not None else ""


# --------------------------------------------------------------------------
# Module-level singleton
# --------------------------------------------------------------------------
_store: LayoutStore | None = None


def store() -> LayoutStore:
    """Lazily build and return the process-wide LayoutStore."""
    global _store
    if _store is None:
        _store = LayoutStore()
    return _store


def reset_singleton_for_tests() -> None:
    """Test helper — drops the cached store so the next ``store()`` call
    rebuilds against a fresh patched ``layout_path()``."""
    global _store
    _store = None
