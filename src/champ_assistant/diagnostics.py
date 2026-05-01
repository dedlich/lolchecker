"""Periodic runtime metrics logger.

Hooks into the existing pipelines (StateStore, RenderScheduler,
LCU/LCDA event consumers) to record:

  * cpu     — process CPU% via psutil sampling
  * mem     — RSS in MB
  * fps     — repaints emitted by the scheduler since last log
  * evt_lat — average event-arrival to state-update latency
  * upd_dt  — average state-update duration

Logged at INFO level every ``interval_s`` (default 10s) so the production
log file gives ops-style visibility without flooding.
"""
from __future__ import annotations

import logging
import statistics
import time

from PyQt6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


class Diagnostics(QObject):
    """Single instance owned by ``__main__`` — wires into the rest."""

    DEFAULT_INTERVAL_S = 10.0

    def __init__(self, *, interval_s: float = DEFAULT_INTERVAL_S) -> None:
        super().__init__()
        self._proc = _process_handle()
        self._timer = QTimer(self)
        self._timer.setInterval(int(interval_s * 1000))
        self._timer.timeout.connect(self._log)
        self._scheduler = None  # type: ignore[var-annotated]
        self._vision = None  # type: ignore[var-annotated]
        self._health_monitor = None  # type: ignore[var-annotated]
        self._event_latencies_ms: list[float] = []
        self._state_update_ms: list[float] = []
        self._last_log = time.monotonic()
        # Prime psutil's interval-based sampler.
        if self._proc is not None:
            try:
                self._proc.cpu_percent(interval=None)
            except Exception:  # noqa: BLE001
                pass

    # -- wiring ------------------------------------------------------------

    def attach_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        self._scheduler = scheduler

    def attach_store(self, store) -> None:  # type: ignore[no-untyped-def]
        store._on_update_metric = self.record_state_update_ms  # internal hook

    def attach_vision(self, vision_service) -> None:  # type: ignore[no-untyped-def]
        """Optional: hook the vision-observation service so its counters
        appear in the periodic [DIAG] line. No-op if vision is disabled."""
        self._vision = vision_service

    def attach_health_monitor(self, health_monitor) -> None:  # type: ignore[no-untyped-def]
        """Attach the process-wide HealthMonitor so unhealthy services are
        surfaced in the periodic [DIAG] log line."""
        self._health_monitor = health_monitor

    def record_event_latency_ms(self, latency_ms: float) -> None:
        """Caller invokes this when an LCU/LCDA event finishes processing,
        passing the wall-clock delta from receipt to state-update commit."""
        self._event_latencies_ms.append(latency_ms)

    def record_state_update_ms(self, duration_ms: float) -> None:
        self._state_update_ms.append(duration_ms)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
            logger.info(
                "diagnostics_started interval=%ds", self._timer.interval() // 1000,
            )

    def stop(self) -> None:
        self._timer.stop()

    # -- internals ---------------------------------------------------------

    def _log(self) -> None:
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_log)
        cpu = mem_mb = -1.0
        if self._proc is not None:
            try:
                cpu = self._proc.cpu_percent(interval=None)
                mem_mb = self._proc.memory_info().rss / 1024 / 1024
            except Exception as exc:  # noqa: BLE001
                logger.debug("psutil_sample_failed: %s", exc)
        fps = 0.0
        if self._scheduler is not None:
            fps = self._scheduler.frame_count / elapsed
            self._scheduler.reset_frame_count()
        avg_evt = _safe_mean(self._event_latencies_ms)
        avg_upd = _safe_mean(self._state_update_ms)
        # Optional vision counters — only included when the vision
        # service is attached (i.e. enable_auto_camp_detection=True).
        vision_part = ""
        if self._vision is not None:
            try:
                vision_part = (
                    f" vision_frames={self._vision.frames_processed}"
                    f" vision_events={self._vision.events_emitted}"
                    f" vision_failures={self._vision.failures}"
                )
            except Exception:  # noqa: BLE001 — diagnostics must never raise
                vision_part = ""
        health_part = ""
        if self._health_monitor is not None:
            try:
                unhealthy = self._health_monitor.all_unhealthy()
                if unhealthy:
                    health_part = f" unhealthy={','.join(unhealthy)}"
            except Exception:  # noqa: BLE001
                health_part = ""
        logger.info(
            "diagnostics cpu=%.1f%% mem=%.0fMB fps=%.2f evt_lat=%.1fms upd_dt=%.2fms%s%s",
            cpu, mem_mb, fps, avg_evt, avg_upd, vision_part, health_part,
        )
        self._event_latencies_ms.clear()
        self._state_update_ms.clear()
        self._last_log = now


def _safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _process_handle():  # type: ignore[no-untyped-def]
    """Return a psutil.Process or None if psutil isn't installed.
    Diagnostics still logs FPS + latencies in the absence of psutil."""
    try:
        import psutil
    except ImportError:
        logger.info("psutil unavailable — CPU/mem metrics disabled")
        return None
    return psutil.Process()
