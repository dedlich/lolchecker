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
    OVERLOAD_FACTOR = 2.0  # warn once we sustain >2× max_fps over the window
    OVERLOAD_WINDOW_S = 1.0  # rolling window for overload detection
    OVERLOAD_LOG_COOLDOWN_S = 5.0  # don't spam the log every frame

    def __init__(
        self,
        *,
        max_fps: int = DEFAULT_MAX_FPS,
        tick_hz: float = 1.0,
    ) -> None:
        super().__init__()
        self._max_fps = max_fps
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
        # Overload detection: ring of recent repaint timestamps.
        self._repaint_window: list[float] = []
        self._last_overload_log = 0.0

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
        now = time.monotonic()
        self._last_repaint = now
        self._record_for_overload(now)
        self.repaint.emit()

    def _fire_tick(self) -> None:
        self.tick.emit()

    def _record_for_overload(self, now: float) -> None:
        """Sliding-window detector: warn if we sustain more than
        ``OVERLOAD_FACTOR`` × ``max_fps`` over ``OVERLOAD_WINDOW_S``.

        Catches the failure mode where a feedback loop (state listener
        calling store.update calling request_repaint calling listener)
        starts firing the repaint coalescer back-to-back. The QTimer
        floor of 8ms keeps the absolute rate bounded, but burning ~125 FPS
        worth of CPU on an idle overlay is still a regression we want to
        surface in production logs.
        """
        cutoff = now - self.OVERLOAD_WINDOW_S
        # Drop expired timestamps (cheap — list is bounded by max_fps × 2).
        while self._repaint_window and self._repaint_window[0] < cutoff:
            self._repaint_window.pop(0)
        self._repaint_window.append(now)

        threshold = self._max_fps * self.OVERLOAD_FACTOR * self.OVERLOAD_WINDOW_S
        if len(self._repaint_window) <= threshold:
            return
        if (now - self._last_overload_log) < self.OVERLOAD_LOG_COOLDOWN_S:
            return
        self._last_overload_log = now
        logger.warning(
            "render overload detected: %d repaints in last %.1fs "
            "(max_fps=%d, threshold=%dx)",
            len(self._repaint_window),
            self.OVERLOAD_WINDOW_S,
            self._max_fps,
            int(self.OVERLOAD_FACTOR),
        )
