"""Centralized state container for the overlay.

Single authoritative source of truth that:
  - Holds an immutable :class:`GameState` snapshot
  - Mutates only via :meth:`StateStore.update` (which produces a new
    GameState via :func:`dataclasses.replace`)
  - Notifies subscribed listeners on each *real* change (no-op updates
    that produce identical state are dropped)
  - Is thread-safe so the LCU/LCDA async pipelines can call it from
    qasync's worker contexts without races

The store is deliberately NOT Qt-aware — it returns plain Python data so
unit tests can drive it without a QApplication. Widgets and the render
scheduler (which IS Qt-aware) subscribe to it.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameState:
    """Single immutable snapshot of everything the overlay needs to render.

    Adding a new field is the recommended way to plumb new data through —
    it gives you free pub/sub, cache-busting, and unit-test isolation.
    """
    # ---- coarse phase ---------------------------------------------------
    phase: str = "idle"  # idle | champ_select | in_game | post_game
    connection_state: str = "disconnected"  # mirror of UI state for status bar

    # ---- timing ---------------------------------------------------------
    game_time: float = 0.0
    last_lcda_received: float = 0.0  # monotonic — used for game_time interp

    # ---- LCU champ-select payloads (forwarded to existing widgets) ----
    session_view: Any = None  # SessionView when in champ_select

    # ---- LCDA in-game payloads (forwarded to existing widgets) --------
    lcda_snapshot: Any = None  # LcdaSnapshot when in_game

    # ---- aggregated user toggles --------------------------------------
    passthrough: bool = False
    main_visible: bool = True

    # ---- diagnostics counters bumped from outside ---------------------
    revision: int = 0  # bumped on every accepted update — for debugging


StateListener = Callable[["GameState", "GameState"], None]


class StateStore:
    """Thread-safe pub/sub for :class:`GameState`."""

    def __init__(self) -> None:
        self._state: GameState = GameState()
        self._listeners: list[StateListener] = []
        self._lock = threading.RLock()
        # Diagnostics hook — wired by Diagnostics.attach(store).
        self._on_update_metric: Optional[Callable[[float], None]] = None

    # -- read --------------------------------------------------------------

    def get(self) -> GameState:
        return self._state

    # -- write -------------------------------------------------------------

    def update(self, **changes: Any) -> GameState:
        """Apply ``changes`` to the current state. Returns the new state.
        No-ops (changes that produce equal state) skip listener notification."""
        if not changes:
            return self._state

        import time as _time
        start = _time.monotonic()

        with self._lock:
            old = self._state
            new = replace(old, revision=old.revision + 1, **changes)
            # Compare ignoring the revision bump so identical content doesn't
            # masquerade as a real change.
            if _equivalent(old, new):
                return old
            self._state = new

        for listener in list(self._listeners):
            try:
                listener(old, new)
            except Exception:  # noqa: BLE001 — never let one listener kill others
                logger.exception("state_listener_failed")

        if self._on_update_metric is not None:
            self._on_update_metric((_time.monotonic() - start) * 1000.0)
        return new

    # -- subscribe ---------------------------------------------------------

    def subscribe(self, listener: StateListener) -> Callable[[], None]:
        """Register a listener; returns an unsubscribe callable."""
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub


def _equivalent(a: GameState, b: GameState) -> bool:
    """Compare two states ignoring the bookkeeping revision counter."""
    return replace(a, revision=0) == replace(b, revision=0)
