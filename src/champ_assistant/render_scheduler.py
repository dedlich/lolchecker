"""Repaint coalescer + frame limiter for the Qt UI thread.

Multiple state changes within one frame interval batch into a single
repaint call. Combined with :class:`StateStore`'s "no-op updates are
silent" policy, this means the UI re-renders exactly when something
visible has actually changed, never in a tight loop.

The scheduler also exposes a 1 Hz "tick" channel that drives game-time
interpolation between LCDA snapshots — widgets read interpolated
``game_time`` from the store but never run their own QTimer.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)


class RenderScheduler(QObject):
    """Throttled repaint dispatcher.

    Two cadences:
      * ``request_repaint()`` — coalesces N requests within ``min_interval_ms``
        into a single ``repaint`` signal emit.
      * ``tick`` (every 1000ms) — interpolates ``game_time`` and notifies
        the store, replacing per-widget QTimers.
    """

    repaint = pyqtSignal()
    tick = pyqtSignal()

    DEFAULT_MAX_FPS = 30  # ceiling — actual rate is event-driven, never higher

    def __init__(
        self,
        *,
        max_fps: int = DEFAULT_MAX_FPS,
        tick_hz: float = 1.0,
    ) -> None:
        super().__init__()
        self._min_interval_ms = max(8, int(1000 / max_fps))
        self._dirty = False

        # Repaint coalescer: single-shot QTimer rearmed on each request.
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.timeout.connect(self._fire_repaint)

        # 1 Hz tick: drives game-time interpolation between LCDA snapshots.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(int(1000.0 / tick_hz))
        self._tick_timer.timeout.connect(self._fire_tick)

        # Diagnostics counters
        self._frame_count = 0
        self._last_repaint = 0.0

    # -- public API --------------------------------------------------------

    def start(self) -> None:
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def stop(self) -> None:
        self._tick_timer.stop()
        self._repaint_timer.stop()

    def request_repaint(self) -> None:
        """Mark the UI dirty. Will fire ``repaint`` after at most
        ``min_interval_ms`` so a burst of state changes only triggers
        one repaint."""
        self._dirty = True
        if not self._repaint_timer.isActive():
            self._repaint_timer.start(self._min_interval_ms)

    # -- diagnostics access -----------------------------------------------

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def reset_frame_count(self) -> None:
        self._frame_count = 0

    # -- internals ---------------------------------------------------------

    def _fire_repaint(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self._frame_count += 1
        self._last_repaint = time.monotonic()
        self.repaint.emit()

    def _fire_tick(self) -> None:
        self.tick.emit()
