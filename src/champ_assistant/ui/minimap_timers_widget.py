"""Transparent overlay that paints camp + objective timers directly
on top of the in-game minimap.

Design
======
Square top-level window, fully transparent background, frameless,
always-on-top, click-through-by-event for areas without markers.
Auto-positions over the bottom-right corner of the LoL game window
on every LCDA tick — tracks the game window if it's moved.

The widget hosts a single ``MapOverlayLayer`` covering its full area;
that layer paints camp markers (R/B/G/K/P/W/S) and major-objective
markers (D/B/H) at their canonical SR positions plus countdowns when
armed. Click-to-arm for jungle camps stays in the layer's own
mousePressEvent; major objectives auto-arm from LCDA kill events.

Honest scope
------------
* No drag-to-move (would conflict with click-to-arm — auto-pinning
  is the source of truth for position).
* No persistent layout — auto-positioning makes saved positions
  meaningless across resolutions.
* Click-through for ALL events isn't possible cross-process without
  Win32 layered window flags; users lose minimap pings within the
  widget area as the trade-off for visible timers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QWidget

from ..jungle_timeline import CampState, JungleTimelineEngine
from ..lcda.objectives import ObjectiveTimer
from .map_overlay_layer import MapOverlayLayer

if TYPE_CHECKING:
    from ..lcda.source import LcdaSnapshot


class MinimapTimersWidget(QWidget):
    """See module docstring."""

    KEY = "minimap_timers"
    # Fallback geometry when the LoL window isn't found (non-Windows,
    # client closed, etc.) — drops in the bottom-right of a 1080p
    # primary screen as a sane last resort.
    DEFAULT_POS = (1640, 800)
    DEFAULT_SIZE = (260, 260)

    def __init__(self) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Translucent background — markers + countdown text paint on
        # top of whatever's behind the widget (the in-game minimap).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._map_layer: MapOverlayLayer | None = None
        self._engine: JungleTimelineEngine | None = None
        self._engine_unsub = None  # type: ignore[var-annotated]
        self._deferred_scheduler = None  # type: ignore[var-annotated]
        self._latest_objectives: dict[str, ObjectiveTimer] = {}
        self._latest_game_time = 0.0

        self.resize(*self.DEFAULT_SIZE)
        self.move(*self.DEFAULT_POS)
        self.hide()

    # -- wiring ----------------------------------------------------------

    def attach_engine(self, engine: JungleTimelineEngine) -> None:
        """Subscribe to the central JungleTimelineEngine. Idempotent."""
        if self._engine_unsub is not None:
            self._engine_unsub()
        self._engine = engine
        self._engine_unsub = engine.subscribe(self._on_camp_states)

        if self._map_layer is None:
            self._map_layer = MapOverlayLayer(engine, parent=self)
            self._map_layer.setGeometry(self.rect())
            self._map_layer.show()
            if self._deferred_scheduler is not None:
                self._map_layer.connect_scheduler(self._deferred_scheduler)
                self._deferred_scheduler = None
        else:
            self._map_layer._engine = engine

        self._on_camp_states(engine.states())

    def connect_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        """Hook the central 1 Hz tick — drives the layer's blink/repaint."""
        if self._map_layer is not None:
            self._map_layer.connect_scheduler(scheduler)
        else:
            self._deferred_scheduler = scheduler

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._map_layer is not None:
            self._map_layer.setGeometry(self.rect())

    # -- public API ------------------------------------------------------

    def update_snapshot(self, snapshot: "LcdaSnapshot | None") -> None:
        """Driven by the LCDA tick. None snapshot → hide. Otherwise:
        re-pin to the in-game minimap (every tick, in case the user
        moved their game window) and forward objective state to the
        layer."""
        if snapshot is None:
            self.hide()
            return
        self._pin_to_game_minimap()
        self._latest_game_time = snapshot.game_time
        self._latest_objectives = {o.name: o for o in snapshot.objectives}
        if self._map_layer is not None:
            self._map_layer.set_objectives(
                self._latest_objectives, snapshot.game_time,
            )
        if not self.isVisible():
            self.show()

    def _pin_to_game_minimap(self) -> None:
        """Move + resize to overlay the LoL window's bottom-right
        minimap area. No-op on non-Windows / when League isn't
        running. Re-runs every tick so the widget tracks game-window
        moves and resolution changes."""
        try:
            from ..lcu.window import find_league_window
            info = find_league_window()
        except Exception:  # noqa: BLE001 — auto-position must never crash
            return
        if info is None:
            return
        edge = max(180, int(info.height * 0.27))
        # The LoL minimap is flush with the bottom-right corner of
        # the game's drawable area. Match that exactly.
        target_x = info.right - edge
        target_y = info.bottom - edge
        # Only reposition when the geometry actually changed — avoids
        # constant flicker on every tick.
        cur = self.geometry()
        if (
            cur.x() == target_x
            and cur.y() == target_y
            and cur.width() == edge
            and cur.height() == edge
        ):
            return
        self.setGeometry(target_x, target_y, edge, edge)
        if self._map_layer is not None:
            self._map_layer.setGeometry(self.rect())

    # -- internals -------------------------------------------------------

    def _on_camp_states(self, states: dict[str, CampState]) -> None:
        # The MapOverlayLayer pulls states directly from its engine
        # reference on every paint. We just need to trigger a repaint.
        if self._map_layer is not None:
            self._map_layer.update()
