"""Ordered startup + shutdown manager + clean-shutdown marker.

The app composes ~6 subsystems (state, scheduler, layout, hotkeys, update,
diagnostics) each with their own resources — a Win32 hotkey table, a Qt
QTimer, a daemon thread, an asyncio task. Production-grade shutdown
requires:

  * deterministic order — stop the producer before the consumer (e.g.
    cancel update task before tearing down the Qt loop, stop hotkey
    listener before the QApplication exits so no WM_HOTKEY arrives at a
    half-destroyed signal),
  * idempotent stop — Qt fires ``aboutToQuit`` exactly once, but we want
    the app to survive multiple shutdown paths (Ctrl+C, tray Quit,
    process group signal, top-level exception),
  * "is_shutting_down" visibility — late callbacks (download progress,
    hotkey signal handler) need to bail fast instead of touching torn-down
    objects.

This module is deliberately Qt-free so it can be unit-tested without a
QApplication. The Qt wiring (``aboutToQuit`` → ``shutdown()``) is done in
``__main__``.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

StopFn = Callable[[], None]


@dataclass
class _Service:
    name: str
    stop: StopFn


class LifecycleManager:
    """Records the canonical startup order; drives shutdown in reverse.

    Usage::

        lifecycle = LifecycleManager()
        lifecycle.register("scheduler", scheduler.stop)
        lifecycle.register("hotkeys",   hotkeys.stop)
        ...
        # On exit:
        lifecycle.shutdown()

    The shutdown walk is fully isolated — each ``stop`` call runs inside
    try/except so one misbehaving subsystem can never block the rest of
    the teardown.
    """

    def __init__(self) -> None:
        self._services: list[_Service] = []
        self._lock = threading.Lock()
        self._shutting_down = False
        self._shutdown_done = False
        # Optional finalizers — called in order AFTER every registered
        # service has stopped. Used by ``__main__`` to plug in the
        # session-summary emit + clean-shutdown marker write so the
        # marker is the absolute last side effect of the shutdown
        # path. Failures don't propagate (each finalizer is wrapped).
        self._finalizers: list[tuple[str, StopFn]] = []

    # -- registration -----------------------------------------------------

    def register(self, name: str, stop: StopFn) -> None:
        """Append a stop callable. Order matters — registration order
        is the assumed startup order; teardown walks in reverse.
        Calling this after ``shutdown()`` has begun is logged + ignored
        so we never resurrect a half-torn-down state."""
        with self._lock:
            if self._shutting_down:
                logger.warning(
                    "lifecycle: register('%s') ignored — shutdown in progress",
                    name,
                )
                return
            self._services.append(_Service(name=name, stop=stop))
            logger.debug("lifecycle: registered %s", name)

    def register_finalizer(self, name: str, fn: StopFn) -> None:
        """Append a finalizer to run after all services have stopped.
        Order matches registration order (NOT reversed) — we want
        session_summary BEFORE the clean-shutdown marker so a crash
        emitting the summary doesn't claim a clean exit."""
        with self._lock:
            if self._shutting_down:
                logger.warning(
                    "lifecycle: register_finalizer('%s') ignored — "
                    "shutdown in progress", name,
                )
                return
            self._finalizers.append((name, fn))

    # -- queries ----------------------------------------------------------

    @property
    def is_shutting_down(self) -> bool:
        """True from the moment ``shutdown()`` is called.
        Background tasks should consult this before doing expensive work
        (downloading an update, applying a state change) so they can
        bail rather than touch torn-down resources."""
        with self._lock:
            return self._shutting_down

    # -- shutdown ---------------------------------------------------------

    def shutdown(self) -> None:
        """Stop every registered service in reverse order. Idempotent —
        a second call is a no-op so the same handler can be wired to
        both Qt's ``aboutToQuit`` and a fallback ``finally`` block."""
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            services = list(reversed(self._services))

        logger.info("lifecycle: shutdown started (%d services)", len(services))
        for svc in services:
            try:
                logger.info("lifecycle: stopping %s", svc.name)
                svc.stop()
            except Exception:  # noqa: BLE001 — never let one stop kill the rest
                logger.exception(
                    "lifecycle: stop(%s) raised — continuing", svc.name,
                )

        # Finalizers run in registration order, after every service has
        # stopped. Each is isolated so a failure (e.g. session_summary
        # logging fails) doesn't prevent the next finalizer (e.g. the
        # clean-shutdown marker write) from running.
        with self._lock:
            finalizers = list(self._finalizers)
        for name, fn in finalizers:
            try:
                logger.info("lifecycle: finalizer %s", name)
                fn()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "lifecycle: finalizer(%s) raised — continuing", name,
                )

        with self._lock:
            self._shutdown_done = True
        logger.info("lifecycle: shutdown complete")
