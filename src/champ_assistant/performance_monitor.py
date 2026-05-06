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
from collections import defaultdict, deque
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
    """Thin delegator to ``app_paths.log_dir`` — kept under the original
    name so existing tests that ``monkeypatch.setattr(pm, "_log_dir", ...)``
    keep working. New code should import from ``app_paths`` directly.
    """
    from . import app_paths
    return app_paths.log_dir()


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
    global _RULE_TIMING_INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
    with _RULE_TIMING_LOCK:
        _RULE_TIMING_INSTANCE = None


# --------------------------------------------------------------------------
# Per-rule timing — Strategy A2 instrumentation
# --------------------------------------------------------------------------
# The decision engine runs 53+ rules every LCDA tick (~0.5 Hz). Without
# per-rule timing the engine is a black box: when a tick spikes past the
# poll interval we can't tell which rule is to blame. This recorder keeps
# a ring buffer of the most recent per-rule durations + emits a digest
# (p50 / p95 / max / count) on flush.
#
# Overhead: ``time.perf_counter()`` is ~30-50 ns. For 53 rules per tick at
# 0.5 Hz that's ~1.6 µs / tick of measurement overhead — well below the
# noise floor of the rules themselves.

# Per-rule samples are kept up to this many before older ones are evicted.
# 500 × 53 rules ≈ 26K floats ≈ 200 kB peak — bounded, comfortable.
RULE_TIMING_RING_SIZE = 500


class RuleTimingRecorder:
    """Per-rule duration + activation recorder.

    Tracks three kinds of state per rule:

    * ``_samples`` — ring buffer of recent durations (for p50/p95/max).
    * ``_invocations`` — lifetime call count. Doesn't decay with the
      ring buffer, so the fire-rate denominator stays accurate even
      across long sessions.
    * ``_fires`` — lifetime count of calls that produced a Recommendation
      (passed ``fired=True``). Numerator for the fire rate.

    Append-only on the hot path; readers (``digest`` / ``flush``)
    snapshot under the lock so concurrent rule eval doesn't tear state.
    """

    def __init__(self) -> None:
        self._samples: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=RULE_TIMING_RING_SIZE),
        )
        self._invocations: dict[str, int] = defaultdict(int)
        self._fires: dict[str, int] = defaultdict(int)
        self._lock = Lock()

    def record(
        self, rule_name: str, duration_ms: float, *, fired: bool = False,
    ) -> None:
        """Append one duration sample + bump invocation counter.

        ``fired=True`` additionally bumps the fire counter — the engine
        passes this when the rule returned a non-None Recommendation.
        Calls that raised count as invocations but not as fires (they
        also didn't produce output).
        """
        with self._lock:
            self._samples[rule_name].append(duration_ms)
            self._invocations[rule_name] += 1
            if fired:
                self._fires[rule_name] += 1

    def snapshot(self) -> dict[str, list[float]]:
        """Defensive copy of the current samples — readers iterate safely."""
        with self._lock:
            return {name: list(samples) for name, samples in self._samples.items()}

    def activation_snapshot(self) -> tuple[dict[str, int], dict[str, int]]:
        """Defensive copy of (invocations, fires) maps."""
        with self._lock:
            return dict(self._invocations), dict(self._fires)

    def digest(self) -> list[tuple[str, int, int, float, float, float, float, float]]:
        """Per-rule digest sorted by descending p95. Empty rules omitted.

        Each row: ``(name, invocations, fires, fire_rate,
        p50_ms, p95_ms, max_ms, mean_ms)``.

        ``fire_rate`` is in [0, 1] — fraction of invocations that produced
        a Recommendation. A high p95 + high fire rate is a real cost; a
        high p95 + low fire rate is a tail-event under specific snapshot
        conditions, less concerning.
        """
        snap_samples = self.snapshot()
        invocations, fires = self.activation_snapshot()
        rows: list[tuple[str, int, int, float, float, float, float, float]] = []
        for name, samples in snap_samples.items():
            if not samples:
                continue
            ordered = sorted(samples)
            count = len(ordered)
            # Index conversions clamped so single-sample buffers don't go
            # off the end (math.floor on float index is the standard
            # nearest-rank percentile).
            p50_idx = max(0, min(count - 1, int(0.5 * count)))
            p95_idx = max(0, min(count - 1, int(0.95 * count)))
            inv = invocations.get(name, count)
            fc = fires.get(name, 0)
            fire_rate = fc / inv if inv else 0.0
            rows.append((
                name,
                inv,
                fc,
                fire_rate,
                ordered[p50_idx],
                ordered[p95_idx],
                ordered[-1],
                sum(ordered) / count,
            ))
        rows.sort(key=lambda r: r[5], reverse=True)
        return rows

    def flush(self, path: Path | None = None) -> Path | None:
        """Write the digest to ``rule_timing.log`` next to performance.log.
        Atomic via tempfile + os.replace so a kill mid-flush doesn't leave a
        half-written log. Returns the path on success, None on I/O failure.

        Output format (TSV)::

          rule  invocations  fires  fire_rate  p50_ms  p95_ms  max_ms  mean_ms
        """
        if path is None:
            path = _log_dir() / "rule_timing.log"
        rows = self.digest()
        if not rows:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.info("rule_timing_log_mkdir_failed: %s", exc)
            return None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False,
                dir=str(path.parent), prefix=".rule_timing_", suffix=".log.tmp",
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(
                    "rule\tinvocations\tfires\tfire_rate"
                    "\tp50_ms\tp95_ms\tmax_ms\tmean_ms\n"
                )
                for name, inv, fc, rate, p50, p95, mx, mean in rows:
                    tmp.write(
                        f"{name}\t{inv}\t{fc}\t{rate:.4f}"
                        f"\t{p50:.4f}\t{p95:.4f}\t{mx:.4f}\t{mean:.4f}\n"
                    )
            os.replace(tmp_path, path)
            return path
        except OSError as exc:
            logger.info("rule_timing_log_write_failed: %s", exc)
            return None


_RULE_TIMING_INSTANCE: RuleTimingRecorder | None = None
_RULE_TIMING_LOCK = Lock()


def rule_timing_recorder() -> RuleTimingRecorder:
    """Return the process-wide rule-timing singleton."""
    global _RULE_TIMING_INSTANCE
    with _RULE_TIMING_LOCK:
        if _RULE_TIMING_INSTANCE is None:
            _RULE_TIMING_INSTANCE = RuleTimingRecorder()
        return _RULE_TIMING_INSTANCE
