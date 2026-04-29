"""Lightweight UX telemetry — append-only event log.

Purpose
=======
This module captures *what the UI actually rendered and when* so we
can answer questions the UX-evaluation pass left open without users:

  * How often does LOW-confidence UI actually appear in real games?
  * Are multiple high-load widgets co-visible during fights?
  * Is anything rendered that's never seen?

It is NOT analytics, NOT a dashboard, NOT live monitoring. It's a
truth-capture layer — events get appended to a JSONL file, an offline
script (``scripts/telemetry_summary.py``) aggregates them later.

Hard performance constraints (per spec)
========================================
  * record() is a deque.append under a lock — microseconds, non-blocking
  * disk flush runs on a 5s QTimer — batched, never on the event path
  * no per-frame hooks — only discrete state transitions emit events
  * disk writes use append-mode O(N) per flush, rotate at 5 MB

Privacy
=======
The event names + payloads explicitly do NOT carry user input, chat,
account data, match identifiers, or any pixel/screen content. Only
internal UI state transitions. See docstring on ``record`` for the
allowed payload shape.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


# Event names — kept as constants so a typo at the call site fails
# loud (NameError) instead of silently writing a misspelled event.
EV_WIDGET_SHOWN          = "widget_shown"
EV_WIDGET_HIDDEN         = "widget_hidden"
EV_WIDGET_FIRST_RENDER   = "widget_first_render"
EV_CONFIDENCE_BAND       = "confidence_band_change"
EV_URGENCY_CHANGE        = "urgency_state_change"
EV_GAME_PHASE_CHANGE     = "game_phase_change"
EV_FIGHT_WINDOW          = "fight_window_detected"
EV_FOCUS                 = "window_focus"           # gain | loss
EV_OVERLAY_TOGGLE        = "overlay_visibility_toggle"
# Vision subsystem (Stage A — color heuristic camp detection)
EV_CAMP_VISIBILITY       = "camp_visibility_detected"
EV_CAMP_CLEAR_INFERRED   = "camp_clear_inferred"


def _state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant"
    return Path.home() / ".champ-assistant"


def telemetry_path() -> Path:
    return _state_dir() / "telemetry.jsonl"


class TelemetryRecorder(QObject):
    """Append-only ring-buffered event recorder.

    Two storage layers: an in-memory bounded deque (for in-process
    inspection / tests / a future debug overlay) and a JSONL file on
    disk that grows append-only between flushes. The deque size caps
    memory; the file size cap rotates the file so a marathon
    24-hour session doesn't write GBs.
    """

    DEFAULT_RING_SIZE       = 10_000
    DEFAULT_FLUSH_INTERVAL_S = 5.0
    DEFAULT_MAX_FILE_BYTES   = 5_000_000   # 5 MB before rotation

    def __init__(
        self,
        *,
        ring_size: int = DEFAULT_RING_SIZE,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        path: Path | None = None,
    ) -> None:
        super().__init__()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._lock = threading.Lock()
        self._path = path or telemetry_path()
        self._max_file_bytes = max_file_bytes

        # Pending queue is what hasn't been flushed yet — separate from
        # the ring (which is for in-memory inspection) so a flush
        # doesn't have to scan the whole ring on each tick.
        self._pending: list[dict[str, Any]] = []

        self._timer = QTimer(self)
        self._timer.setInterval(int(flush_interval_s * 1000))
        self._timer.timeout.connect(self._flush)

        self._session_start_ts = time.time()

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
            logger.info("telemetry started; flush every %ds", self._timer.interval() // 1000)

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
        # Final flush so the last few seconds aren't lost on a clean exit.
        self._flush()

    # -- input ------------------------------------------------------------

    def record(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """Capture an event. Non-blocking: appends to in-memory deque
        and pending-flush list. The caller's call site sees only a
        deque.append + list.append under a single lock.

        Payload must be JSON-serializable and contain ONLY internal
        UI/state values (no user input, chat, account data, etc. —
        see module docstring for the privacy contract)."""
        entry = {
            "ts": time.time(),
            "event": event,
            "payload": dict(payload) if payload else {},
        }
        with self._lock:
            self._ring.append(entry)
            self._pending.append(entry)

    # -- inspection -------------------------------------------------------

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Snapshot of the most-recent N entries, newest last. Used by
        tests and any future in-app debug overlay. Cheap copy."""
        with self._lock:
            return list(self._ring)[-limit:]

    @property
    def session_start_ts(self) -> float:
        return self._session_start_ts

    # -- internals --------------------------------------------------------

    def _flush(self) -> None:
        with self._lock:
            if not self._pending:
                return
            batch, self._pending = self._pending, []

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Rotate before writing if the file is already at the cap.
            if self._path.is_file() and self._path.stat().st_size >= self._max_file_bytes:
                self._rotate()
            with self._path.open("a", encoding="utf-8") as fh:
                for entry in batch:
                    # default=str so an accidentally-non-JSON-serializable
                    # payload (e.g. a bare object()) doesn't take down
                    # the entire batch — UI safety > pristine telemetry.
                    fh.write(json.dumps(entry, separators=(",", ":"), default=str) + "\n")
        except OSError as exc:
            logger.warning("telemetry flush failed: %s — dropping batch", exc)

    def _rotate(self) -> None:
        """Move the current file to ``.1`` (overwriting any prior .1)
        so the log is bounded but at least one prior session survives.
        Single backup is enough — telemetry is best-effort, not a
        ledger."""
        backup = self._path.with_suffix(self._path.suffix + ".1")
        try:
            if backup.is_file():
                backup.unlink()
            self._path.rename(backup)
            logger.info("telemetry rotated %s -> %s", self._path.name, backup.name)
        except OSError as exc:
            logger.warning("telemetry rotation failed: %s", exc)


# --------------------------------------------------------------------------
# Singleton — one recorder per process
# --------------------------------------------------------------------------
_recorder: TelemetryRecorder | None = None


def recorder() -> TelemetryRecorder:
    """Lazy global recorder. Tests use ``reset_singleton_for_tests`` to
    isolate state between cases."""
    global _recorder
    if _recorder is None:
        _recorder = TelemetryRecorder()
    return _recorder


def reset_singleton_for_tests() -> None:
    global _recorder
    _recorder = None


# --------------------------------------------------------------------------
# Helpers — collapse repeated hookup patterns at call sites
# --------------------------------------------------------------------------
def make_band_tracker() -> Callable[[dict], None]:
    """Return a ``states_callback`` for ``JungleTimelineEngine.subscribe``
    that emits ``confidence_band_change`` events when any camp's band
    flips. Tracks per-camp last-known band internally so the same band
    isn't re-emitted on every tick.

    Why a closure instead of a class: every call site that wants band
    tracking constructs one (per session) and passes it into the
    engine — no shared state across sessions, GC'd on session end.
    """
    last_band: dict[str, str] = {}

    def _on_states(states: dict) -> None:
        rec = recorder()
        for camp_id, st in states.items():
            band = _band_label(st.confidence)
            prev = last_band.get(camp_id)
            if prev is None:
                last_band[camp_id] = band
                continue
            if prev != band:
                last_band[camp_id] = band
                rec.record(
                    EV_CONFIDENCE_BAND,
                    {"camp_id": camp_id, "from": prev, "to": band},
                )

    return _on_states


def _band_label(confidence: float) -> str:
    """Mirror ui.minimap_timers_widget._band_for, lifted here so the
    telemetry layer doesn't import UI code (telemetry must work in
    headless test runs without QWidget instantiation)."""
    if confidence >= 0.8:
        return "HIGH"
    if confidence >= 0.4:
        return "MID"
    return "LOW"


def make_fight_window_detector(
    *, threshold: int = 3, window_s: float = 10.0,
) -> Callable[[list[dict]], None]:
    """Return a callback that takes the cumulative LCDA event list each
    tick and emits ``fight_window_detected`` when the heuristic fires.

    Heuristic (from spec): a fight window is true if ≥``threshold`` new
    LCDA events arrived within ``window_s`` seconds, OR if any of the
    events is an objective kill (Dragon/Baron/Herald). Objective events
    are sparse so they don't dominate the threshold count by themselves.

    Edge-triggered: emits on transition into the fight state and on
    transition back to non-fight, not every tick.
    """
    seen_ids: set[int] = set()
    recent_ts: deque[float] = deque()
    in_fight = False
    OBJECTIVE_NAMES = {"DragonKill", "BaronKill", "HeraldKill"}

    def _on_events(events: list[dict]) -> None:
        nonlocal in_fight
        now = time.time()
        objective_hit = False
        for e in events:
            event_id = e.get("EventID")
            if not isinstance(event_id, int) or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            recent_ts.append(now)
            if e.get("EventName") in OBJECTIVE_NAMES:
                objective_hit = True

        # Drop expired timestamps from the sliding window.
        cutoff = now - window_s
        while recent_ts and recent_ts[0] < cutoff:
            recent_ts.popleft()

        is_fight = objective_hit or len(recent_ts) >= threshold
        if is_fight != in_fight:
            in_fight = is_fight
            recorder().record(EV_FIGHT_WINDOW, {"active": is_fight})

    return _on_events
