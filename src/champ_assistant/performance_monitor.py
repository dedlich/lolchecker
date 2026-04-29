"""Performance baseline monitor (Strategy A1 — Fastest).

Records named phase timestamps from process start, periodically flushes
to ``performance.log`` so we can audit startup time, service-init
time, and first-render latency. Detection only — no optimization
decisions live here. The charter's rule is "always measure before
optimizing"; this is the measurement.

Design
======
* Singleton accessor ``monitor()`` — one instance per process.
* In-memory ring buffer (cap 200 entries) so a long-running session
  can't eat unbounded RAM. Older phases age out.
* ``record_phase(name)`` is non-blocking; the only I/O happens in
  ``flush()`` which the lifecycle manager calls on shutdown (and
  app code may call after major boot milestones to capture data
  before a potential crash).
* Atomic disk writes (tempfile + os.replace) so a kill mid-flush
  doesn't leave a half-written log.

Honest scope (V1)
-----------------
Measurement only. Frame-time histograms, render-latency profiling,
and service-restart counters are charter steps A2/A4/C2 — separate
work.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Final

logger = logging.getLogger(__name__)

# Process-start timestamp. Captured at module import so phases logged
# during startup measure from a sane baseline, not the time of the
# first record_phase call.
_PROCESS_START: Final[float] = time.perf_counter()

# Buffer cap — keeps the on-disk log readable for the typical multi-
# game session without growing forever.
RING_BUFFER_SIZE = 200


@dataclass(frozen=True)
class PhaseRecord:
    """One observed phase. ``elapsed_ms`` is from process start so
    phases form a strictly-increasing timeline."""
    name: str
    elapsed_ms: float
    wall_clock: float  # time.time() — useful when correlating against logs


def _log_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ChampAssistant" / "logs"
    return Path.home() / ".champ-assistant" / "logs"


def performance_log_path() -> Path:
    return _log_dir() / "performance.log"


class PerformanceMonitor:
    """Records named phase timestamps; flushes to disk on demand.

    All methods are safe to call from any thread — internal Lock
    serializes appends + flushes. Callers should NOT block waiting
    for flush(); the typical pattern is fire-and-forget record_phase
    on the hot path, periodic flush from a background tick or
    shutdown hook.
    """

    def __init__(self) -> None:
        self._buffer: deque[PhaseRecord] = deque(maxlen=RING_BUFFER_SIZE)
        self._lock = Lock()

    def record_phase(self, name: str) -> PhaseRecord:
        """Append a record with the current elapsed-ms-since-start.
        Returns the record so callers can use it for ad-hoc reporting."""
        record = PhaseRecord(
            name=name,
            elapsed_ms=(time.perf_counter() - _PROCESS_START) * 1000.0,
            wall_clock=time.time(),
        )
        with self._lock:
            self._buffer.append(record)
        return record

    def snapshot(self) -> list[PhaseRecord]:
        """Return a defensive copy of the current buffer."""
        with self._lock:
            return list(self._buffer)

    def flush(self) -> Path | None:
        """Write the current buffer to ``performance.log``. Atomic via
        tempfile + os.replace. Returns the log path on success, None
        on any I/O failure (which is logged at info — performance
        logging must never crash the app)."""
        path = performance_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.info("performance_log_mkdir_failed: %s", exc)
            return None

        with self._lock:
            records = list(self._buffer)
        if not records:
            return None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False,
                dir=str(path.parent), prefix=".perf_", suffix=".log.tmp",
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write("phase\telapsed_ms\twall_clock\n")
                for record in records:
                    tmp.write(
                        f"{record.name}\t{record.elapsed_ms:.2f}"
                        f"\t{record.wall_clock:.6f}\n"
                    )
            os.replace(tmp_path, path)
            return path
        except OSError as exc:
            logger.info("performance_log_write_failed: %s", exc)
            return None


_INSTANCE: PerformanceMonitor | None = None
_INSTANCE_LOCK = Lock()


def monitor() -> PerformanceMonitor:
    """Return the process-wide singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = PerformanceMonitor()
        return _INSTANCE


def record_phase(name: str) -> PhaseRecord:
    """Convenience wrapper around ``monitor().record_phase(name)``.
    Cheap enough to drop in liberally on the hot path — ~1 µs per
    call in the worst case."""
    return monitor().record_phase(name)


def reset_for_tests() -> None:
    """Test-only: drop the singleton so each test starts clean."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
