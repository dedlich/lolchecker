"""Single structured log line at end-of-session.

Pulls running counters from each subsystem (lifecycle uptime,
diagnostics samples, render scheduler frame_count, telemetry event
ring, state store revision) and emits one ``[SESSION] ...`` log entry.

Tolerant of missing subsystems: every getattr is guarded so a
session that died before scheduler.start() still produces a partial
summary instead of NoneType errors during the dying gasp.

Constant-time computation (one tuple of getattr lookups + one log
call). Never blocks shutdown. Failures are logged at WARNING and
swallowed — a missing summary is strictly better than a hung shutdown.
"""
from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _safe(obj: Any, attr: str, default: Any = None) -> Any:
    """getattr that never raises and never invokes properties that
    raise. The session may be in a half-torn-down state when summary
    fires, so we treat any failure as a missing field."""
    if obj is None:
        return default
    try:
        return getattr(obj, attr, default)
    except Exception:  # noqa: BLE001
        return default


def _safe_callable(obj: Any, attr: str, default: Any = None) -> Any:
    """Like ``_safe`` but for methods — calls them with no args and
    returns the result, or the default on any failure."""
    fn = _safe(obj, attr)
    if not callable(fn):
        return default
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def emit_session_summary(
    *,
    uptime_seconds: float,
    diagnostics: Any = None,
    scheduler: Any = None,
    telemetry_recorder: Any = None,
    state_store: Any = None,
    safe_mode: bool = False,
    crash_count: int = 0,
) -> None:
    """Emit ``[SESSION] duration=...`` to the standard logger.

    ``crash_count`` is informational — at clean shutdown it's always 0,
    but we accept it as a parameter so a future caller could log a
    crash-during-shutdown count without restructuring this API.

    Never raises.
    """
    try:
        # All counters extracted via safe getters so a None subsystem
        # at any position produces a 0/empty value, not an exception.
        frame_count = _safe(scheduler, "frame_count", 0)
        # State-update count: GameState.revision is bumped on every
        # accepted update — the state_store exposes the live snapshot
        # via .get(); we read .revision off it.
        latest_state = _safe_callable(state_store, "get")
        state_updates = _safe(latest_state, "revision", 0)
        # Telemetry event count: read from the ring length if we can.
        recent_events = _safe_callable(telemetry_recorder, "recent", [])
        telemetry_events = len(recent_events) if isinstance(recent_events, list) else 0
        # Memory peak: diagnostics doesn't track peak directly, but it
        # has its most-recent sample in process.memory_info if psutil.
        # We expose a best-effort current value rather than a true peak.
        max_memory_mb = _current_memory_mb(diagnostics)
        avg_fps = (
            frame_count / uptime_seconds
            if uptime_seconds > 0 else 0.0
        )

        logger.info(
            "[SESSION] duration=%.0fs avg_fps=%.1f max_memory_mb=%.0f "
            "state_updates=%d telemetry_events=%d render_frames=%d "
            "crashes=%d safe_mode=%s",
            uptime_seconds,
            avg_fps,
            max_memory_mb,
            state_updates,
            telemetry_events,
            frame_count,
            crash_count,
            "true" if safe_mode else "false",
        )
    except Exception:  # noqa: BLE001 — never block shutdown
        logger.warning("session_summary: emit failed (non-fatal)")


def _current_memory_mb(diagnostics: Any) -> float:
    """Best-effort RSS from diagnostics' psutil.Process handle. Returns
    0.0 if psutil isn't available or the diagnostics object is missing
    its handle."""
    if diagnostics is None:
        return 0.0
    proc = _safe(diagnostics, "_proc")
    if proc is None:
        return 0.0
    try:
        return proc.memory_info().rss / 1024 / 1024
    except Exception:  # noqa: BLE001
        return 0.0


# --------------------------------------------------------------------------
# Uptime helper — small wrapper so __main__ doesn't have to track its
# own start time. Used as a default if no explicit uptime is passed.
# --------------------------------------------------------------------------
class UptimeClock:
    """Monotonic uptime tracker. Construct at startup, read at shutdown."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed(self) -> float:
        return max(0.0, time.monotonic() - self._start)
