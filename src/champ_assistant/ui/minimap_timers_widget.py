"""Transparent overlay that paints camp + objective timers directly
on top of the in-game minimap.

Design
======
Square top-level window, fully transparent background, frameless,
always-on-top, click-through to the game underneath. Auto-positions
over the bottom-right corner of the LoL game window on every LCDA tick
— tracks the game window if it's moved.

Calibration mode (F8 toggle)
----------------------------
Pressing F8 disables click-through and shows a visible dashed border
so the user can drag the widget to align it with their actual minimap.
Bottom-right ``RESIZE_HANDLE_PX`` corner resizes. Press F8 again to
lock — geometry persists to ``overlay.json`` and the auto-pin stops
overriding it.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QResizeEvent
from PyQt6.QtWidgets import QWidget

from .. import layout as layout_module
from ..jungle_timeline import CampState, JungleTimelineEngine
from ..lcda.objectives import ObjectiveTimer
from .map_overlay_layer import MapOverlayLayer

if TYPE_CHECKING:
    from ..lcda.source import LcdaSnapshot

logger = logging.getLogger(__name__)


class MinimapTimersWidget(QWidget):
    """See module docstring."""

    KEY = "minimap_timers"
    # Fallback geometry when the LoL window isn't found (non-Windows,
    # client closed, etc.) — drops in the bottom-right of a 1080p
    # primary screen as a sane last resort.
    DEFAULT_POS = (1640, 800)
    DEFAULT_SIZE = (260, 260)
    # Calibration mode: bottom-right corner that triggers resize.
    RESIZE_HANDLE_PX = 16

    def __init__(self) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        # Translucent background — countdown text paints on top of
        # whatever's behind the widget (the in-game minimap).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Cross-process click-through: WS_EX_TRANSPARENT lets clicks pass
        # to the LoL game window underneath. Required for minimap pings
        # and right-click move commands to keep working through the overlay.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._installed_winflags = False

        self._map_layer: MapOverlayLayer | None = None
        self._engine: JungleTimelineEngine | None = None
        self._engine_unsub = None  # type: ignore[var-annotated]
        self._deferred_scheduler = None  # type: ignore[var-annotated]
        self._latest_objectives: dict[str, ObjectiveTimer] = {}
        self._latest_game_time = 0.0

        # Calibration state.
        self._calibration_mode = False
        self._drag_origin: QPoint | None = None
        self._resize_origin: QPoint | None = None
        self._resize_start_size: tuple[int, int] | None = None
        # Geometry override loaded from overlay.json. When set, auto-pin
        # is skipped so the user's manual position is respected.
        self._geom_override: tuple[int, int, int, int] | None = self._load_override()
        # F8 toggle poll — debounced so a long press doesn't rapid-fire.
        self._f8_was_down = False
        self._f8_timer = QTimer(self)
        self._f8_timer.setInterval(80)
        self._f8_timer.timeout.connect(self._poll_calibration_hotkey)
        self._f8_timer.start()

        if self._geom_override is not None:
            x, y, w, h = self._geom_override
            self.setGeometry(x, y, w, h)
        else:
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
            # Tell the layer which side the local player is on so the
            # camp/objective coords flip correctly when on Chaos.
            allies = list(getattr(snapshot, "allies", []) or [])
            active = getattr(snapshot, "active_summoner", "")
            local = next(
                (p for p in allies if p.summoner_name == active),
                None,
            )
            if local is not None:
                self._map_layer.set_team(local.team)
        if not self.isVisible():
            self.show()

    def _pin_to_game_minimap(self) -> None:
        """Move + resize to overlay the LoL window's bottom-right
        minimap area. Skipped while calibrating or when the user has
        a saved geometry override. No-op on non-Windows / when League
        isn't running."""
        # Always apply click-through flag once HWND is real, regardless
        # of whether we're currently auto-pinning — so toggling out of
        # calibration restores click-through immediately.
        if not self._installed_winflags and not self._calibration_mode:
            self._installed_winflags = self._apply_clickthrough()

        # User-locked position takes priority over auto-pinning.
        if self._calibration_mode or self._geom_override is not None:
            return

        try:
            from ..lcu.window import find_league_window
            info = find_league_window()
        except Exception:  # noqa: BLE001 — auto-position must never crash
            return
        if info is None:
            return
        # Minimap container is ~27% of game height by default (League's
        # default minimap-scale slider). LoL anchors it flush to the
        # bottom-right corner, so we do too — no inset.
        edge = max(180, int(info.height * 0.27))
        target_x = info.right - edge
        target_y = info.bottom - edge
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

    def _apply_clickthrough(self) -> bool:
        """Set the layered + click-through + no-activate flags on the native
        HWND. Mouse events fall through to LoL underneath; the overlay can
        never steal focus from the game even if the user clicks on it.
        Returns True on success (or non-Windows = nothing to do)."""
        import sys
        if not sys.platform.startswith("win"):
            return True
        try:
            import ctypes
            hwnd = int(self.winId())
            if hwnd == 0:
                return False
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED     = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE  = 0x08000000
            WS_EX_TOOLWINDOW  = 0x00000080
            cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                cur | WS_EX_LAYERED | WS_EX_TRANSPARENT
                    | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
            return True
        except Exception:  # noqa: BLE001 — degrade gracefully
            return False

    # -- calibration mode ------------------------------------------------

    def _poll_calibration_hotkey(self) -> None:
        """Toggle calibration on/off when F8 is pressed (Windows-only)."""
        import sys
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            VK_F8 = 0x77
            down = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
            if down and not self._f8_was_down:
                self._toggle_calibration()
            self._f8_was_down = down
        except Exception:  # noqa: BLE001 — never crash from a hotkey poll
            return

    def _toggle_calibration(self) -> None:
        if self._calibration_mode:
            self._exit_calibration()
        else:
            self._enter_calibration()

    def _enter_calibration(self) -> None:
        """Disable click-through, show visible border, allow drag+resize."""
        import sys
        self._calibration_mode = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        # Strip Win32 click-through from the native HWND.
        if sys.platform.startswith("win"):
            try:
                import ctypes
                hwnd = int(self.winId())
                if hwnd:
                    user32 = ctypes.windll.user32
                    GWL_EXSTYLE = -20
                    WS_EX_TRANSPARENT = 0x00000020
                    cur = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur & ~WS_EX_TRANSPARENT)
                self._installed_winflags = False
            except Exception:  # noqa: BLE001
                pass
        logger.info("minimap_calibration enter at (%d,%d) %dx%d",
                    self.x(), self.y(), self.width(), self.height())
        self.update()

    def _exit_calibration(self) -> None:
        """Lock the new geometry, restore click-through, persist."""
        self._calibration_mode = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._installed_winflags = self._apply_clickthrough()
        # Snapshot and persist current geometry.
        x, y, w, h = self.x(), self.y(), self.width(), self.height()
        self._geom_override = (x, y, w, h)
        self._save_override()
        logger.info("minimap_calibration locked at (%d,%d) %dx%d", x, y, w, h)
        self.update()

    def _load_override(self) -> tuple[int, int, int, int] | None:
        """Read [x, y, w, h] from overlay.json's floating_positions if present."""
        try:
            from ..overlay_config import load
            cfg = load()
            v = (cfg.floating_positions or {}).get(self.KEY)
            if isinstance(v, (list, tuple)) and len(v) == 4:
                return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))
        except Exception:  # noqa: BLE001 — never crash from config load
            return None
        return None

    def _save_override(self) -> None:
        """Persist [x, y, w, h] to overlay.json."""
        if self._geom_override is None:
            return
        try:
            from ..overlay_config import load, save
            cfg = load()
            positions = dict(cfg.floating_positions or {})
            positions[self.KEY] = list(self._geom_override)
            cfg.floating_positions = positions
            save(cfg)
        except Exception:  # noqa: BLE001 — best-effort
            logger.exception("minimap_calibration_save_failed")

    # -- mouse drag / resize (only active in calibration) ---------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if not self._calibration_mode or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        local = event.position().toPoint()
        # Bottom-right corner = resize handle.
        if (
            local.x() >= self.width() - self.RESIZE_HANDLE_PX
            and local.y() >= self.height() - self.RESIZE_HANDLE_PX
        ):
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_start_size = (self.width(), self.height())
        else:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        if self._resize_origin is not None and self._resize_start_size is not None:
            delta = event.globalPosition().toPoint() - self._resize_origin
            sw, sh = self._resize_start_size
            new_w = max(80, sw + delta.x())
            new_h = max(80, sh + delta.y())
            # Keep square aspect — average the two deltas so the user
            # can drag in any direction and the widget stays square.
            edge = (new_w + new_h) // 2
            self.resize(edge, edge)
            if self._map_layer is not None:
                self._map_layer.setGeometry(self.rect())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        self._drag_origin = None
        self._resize_origin = None
        self._resize_start_size = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if not self._calibration_mode:
            return
        painter = QPainter(self)
        try:
            # Visible dashed cyan border so the user can see the bounds.
            pen_color = QColor(0, 220, 255, 220)
            painter.setPen(pen_color)
            painter.setBrush(QColor(0, 220, 255, 25))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            # Resize handle marker — small filled triangle in BR corner.
            painter.setBrush(pen_color)
            handle = QRect(
                self.width() - self.RESIZE_HANDLE_PX,
                self.height() - self.RESIZE_HANDLE_PX,
                self.RESIZE_HANDLE_PX,
                self.RESIZE_HANDLE_PX,
            )
            painter.fillRect(handle, pen_color)
            # Hint text.
            painter.setPen(QColor(0, 0, 0, 230))
            painter.drawText(
                self.rect().adjusted(8, 8, -8, -8),
                int(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft),
                "F8 to lock\nDrag to move\nBR corner to resize",
            )
        finally:
            painter.end()

    # -- internals -------------------------------------------------------

    def _on_camp_states(self, states: dict[str, CampState]) -> None:
        # The MapOverlayLayer pulls states directly from its engine
        # reference on every paint. We just need to trigger a repaint.
        if self._map_layer is not None:
            self._map_layer.update()
