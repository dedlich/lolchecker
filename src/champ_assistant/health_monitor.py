"""Health monitor + auto-recovery (Strategy C2 + C5).

Tracks per-service failure / recovery events and triggers a registered
restart callback after N consecutive failures, with exponential backoff
so a permanently-broken service doesn't burn CPU in a tight retry
loop.

Architecture
============
* Pure Python, no Qt, no I/O at construction. Background event loop
  not required — services call ``report_failure`` / ``report_recovery``
  inline; the monitor decides synchronously whether to invoke the
  registered restart callback.
* One registry per process (singleton via ``monitor()``); services
  identify themselves by string name. Name choice is conventional:
  ``"lcda_source"``, ``"lcu_ws"``, etc.
* Restart callbacks are optional. When omitted the monitor still
  tracks failure counts (useful for diagnostics + the future B-pillar
  decision engine to know when a data source is unreliable).

Charter alignment
=================
* C2 step: "service failures, restart counts, latency spikes, dropped
  events. Triggers automatic recovery when possible."
* C5 step: "restart failed services, restore state, maintain user
  session." The restart-callback indirection is how this stays
  decoupled — services own their own restart logic; the monitor only
  decides WHEN to call it.

Honest scope (V1)
-----------------
Latency-spike + dropped-event detection are NOT in V1. They need
metric ingestion paths the rest of the codebase doesn't have yet
(LCDA emits polled snapshots, not latency stamps). Will add when
A2-A4 surfaces them.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Final

logger = logging.getLogger(__name__)

# Default thresholds — tuned for "obvious failure" vs "transient hiccup".
# 3 consecutive failures within the cooldown window flips a service to
# unhealthy and (if a callback is registered) triggers recovery. The
# initial backoff doubles each retry, capped to keep recovery attempts
# from being eternal.
DEFAULT_FAILURE_THRESHOLD: Final[int] = 3
DEFAULT_INITIAL_BACKOFF_S: Final[float] = 1.0
DEFAULT_MAX_BACKOFF_S: Final[float] = 60.0
# After this much idle time without a failure, the consecutive counter
# resets — a once-an-hour transient shouldn't accumulate to "unhealthy".
COUNTER_RESET_AFTER_S: Final[float] = 300.0

RestartCallback = Callable[[], None]


@dataclass
class ServiceHealth:
    """Live state for one watched service. Mutable on purpose — the
    monitor reads + writes through the lock."""
    name: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_recoveries: int = 0
    restart_attempts: int = 0
    last_failure_at: float | None = None
    last_recovery_at: float | None = None
    last_restart_at: float | None = None
    next_eligible_restart_at: float | None = None
    last_error: str | None = None
    restart_callback: RestartCallback | None = None
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD

    @property
    def is_unhealthy(self) -> bool:
        return self.consecutive_failures >= self.failure_threshold


class HealthMonitor:
    """Process-wide service health registry.

    Methods are thread-safe. ``report_failure`` returns whether a
    restart was triggered so callers can log + telemetry the recovery
    attempt at the call site (the monitor itself only logs at WARNING
    on threshold-cross).
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        initial_backoff_s: float = DEFAULT_INITIAL_BACKOFF_S,
        max_backoff_s: float = DEFAULT_MAX_BACKOFF_S,
    ) -> None:
        self._clock = clock
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s
        self._lock = Lock()
        self._services: dict[str, ServiceHealth] = {}

    # -- registration ----------------------------------------------------

    def register_service(
        self,
        name: str,
        *,
        restart_callback: RestartCallback | None = None,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        """Make ``name`` known to the monitor. Optional restart callback
        is invoked when consecutive failures hit ``failure_threshold``
        (with exponential backoff between attempts). Re-registering an
        existing name updates its callback / threshold without resetting
        accumulated counters."""
        with self._lock:
            if name in self._services:
                self._services[name].restart_callback = restart_callback
                self._services[name].failure_threshold = failure_threshold
                return
            self._services[name] = ServiceHealth(
                name=name,
                restart_callback=restart_callback,
                failure_threshold=failure_threshold,
            )

    # -- event ingestion -------------------------------------------------

    def report_failure(self, name: str, exc: BaseException | str | None = None) -> bool:
        """A service operation failed. Increments the consecutive
        counter; if threshold reached AND backoff allows, invokes the
        registered restart callback. Returns True iff a restart was
        invoked on this call."""
        now = self._clock()
        message = self._format_error(exc)
        triggered = False
        callback: RestartCallback | None = None

        with self._lock:
            service = self._services.setdefault(
                name, ServiceHealth(name=name),
            )
            # Reset consecutive counter if last failure is ancient —
            # a once-in-five-minutes transient isn't a real outage.
            if (
                service.last_failure_at is not None
                and now - service.last_failure_at > COUNTER_RESET_AFTER_S
            ):
                service.consecutive_failures = 0
            service.consecutive_failures += 1
            service.total_failures += 1
            service.last_failure_at = now
            service.last_error = message

            if (
                service.is_unhealthy
                and service.restart_callback is not None
                and self._restart_eligible(service, now)
            ):
                triggered = True
                callback = service.restart_callback
                service.restart_attempts += 1
                service.last_restart_at = now
                service.next_eligible_restart_at = now + self._next_backoff(
                    service.restart_attempts,
                )

        if triggered and callback is not None:
            logger.warning(
                "health_monitor_recovery_triggered service=%s "
                "consecutive=%d total_failures=%d last_error=%s",
                name,
                self._services[name].consecutive_failures,
                self._services[name].total_failures,
                message,
            )
            try:
                callback()
            except Exception:  # noqa: BLE001
                logger.exception("health_monitor_restart_callback_crashed name=%s", name)
        return triggered

    def report_recovery(self, name: str) -> None:
        """A service operation succeeded. Resets the consecutive
        counter so the next isolated transient doesn't immediately
        flip the service back to unhealthy."""
        now = self._clock()
        with self._lock:
            service = self._services.setdefault(
                name, ServiceHealth(name=name),
            )
            if service.consecutive_failures > 0:
                service.total_recoveries += 1
            service.consecutive_failures = 0
            service.last_recovery_at = now
            service.last_error = None

    # -- introspection ---------------------------------------------------

    def health(self, name: str) -> ServiceHealth | None:
        """Defensive copy of the named service's state, or None when
        unknown."""
        with self._lock:
            service = self._services.get(name)
            if service is None:
                return None
            return ServiceHealth(
                name=service.name,
                consecutive_failures=service.consecutive_failures,
                total_failures=service.total_failures,
                total_recoveries=service.total_recoveries,
                restart_attempts=service.restart_attempts,
                last_failure_at=service.last_failure_at,
                last_recovery_at=service.last_recovery_at,
                last_restart_at=service.last_restart_at,
                next_eligible_restart_at=service.next_eligible_restart_at,
                last_error=service.last_error,
                restart_callback=service.restart_callback,
                failure_threshold=service.failure_threshold,
            )

    def all_unhealthy(self) -> list[str]:
        """Names of services currently above their failure threshold."""
        with self._lock:
            return [s.name for s in self._services.values() if s.is_unhealthy]

    # -- internals -------------------------------------------------------

    def _restart_eligible(self, service: ServiceHealth, now: float) -> bool:
        if service.next_eligible_restart_at is None:
            return True
        return now >= service.next_eligible_restart_at

    def _next_backoff(self, attempt: int) -> float:
        # attempt=1 (first restart) → initial_backoff
        # attempt=2 → 2× initial
        # attempt=3 → 4× initial
        # ...capped at max_backoff
        scale = 2 ** max(0, attempt - 1)
        return min(self._max_backoff_s, self._initial_backoff_s * scale)

    @staticmethod
    def _format_error(exc: BaseException | str | None) -> str | None:
        if exc is None:
            return None
        if isinstance(exc, str):
            return exc
        return f"{type(exc).__name__}: {exc}"


_INSTANCE: HealthMonitor | None = None
_INSTANCE_LOCK = Lock()


def monitor() -> HealthMonitor:
    """Return the process-wide singleton."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = HealthMonitor()
        return _INSTANCE


def reset_for_tests() -> None:
    """Test-only: drop the singleton so each test starts clean."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None
