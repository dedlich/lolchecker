"""Tests for the health monitor + auto-recovery (charter C2 + C5)."""
from __future__ import annotations

import pytest

from champ_assistant.health_monitor import (
    COUNTER_RESET_AFTER_S,
    DEFAULT_FAILURE_THRESHOLD,
    HealthMonitor,
)


class _FakeClock:
    """Manually advanced clock for deterministic backoff tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def monitor(clock):
    return HealthMonitor(clock=clock, initial_backoff_s=1.0, max_backoff_s=8.0)


# ----------------------------------------------------------------------
# Failure tracking
# ----------------------------------------------------------------------
def test_unknown_service_starts_at_zero(monitor) -> None:
    assert monitor.health("nope") is None


def test_register_creates_health_record(monitor) -> None:
    monitor.register_service("lcda")
    h = monitor.health("lcda")
    assert h is not None
    assert h.consecutive_failures == 0


def test_report_failure_increments_counters(monitor) -> None:
    monitor.register_service("lcda")
    monitor.report_failure("lcda", "boom")
    h = monitor.health("lcda")
    assert h.consecutive_failures == 1
    assert h.total_failures == 1
    assert h.last_error == "boom"


def test_report_recovery_resets_consecutive(monitor) -> None:
    monitor.register_service("lcda")
    monitor.report_failure("lcda")
    monitor.report_failure("lcda")
    monitor.report_recovery("lcda")
    h = monitor.health("lcda")
    assert h.consecutive_failures == 0
    assert h.total_recoveries == 1
    assert h.last_error is None


def test_report_failure_auto_registers_unknown_service(monitor) -> None:
    """Services that haven't been explicitly registered (e.g. opt-in
    diagnostics from a third-party module) still get tracked. No
    restart callback though — that requires explicit registration."""
    monitor.report_failure("ad_hoc")
    h = monitor.health("ad_hoc")
    assert h is not None
    assert h.consecutive_failures == 1


# ----------------------------------------------------------------------
# Threshold + restart callback
# ----------------------------------------------------------------------
def test_restart_fires_on_threshold(monitor) -> None:
    calls: list[None] = []
    monitor.register_service("lcda", restart_callback=lambda: calls.append(None))
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        monitor.report_failure("lcda")
    assert len(calls) == 1


def test_restart_does_not_fire_below_threshold(monitor) -> None:
    calls: list[None] = []
    monitor.register_service("lcda", restart_callback=lambda: calls.append(None))
    for _ in range(DEFAULT_FAILURE_THRESHOLD - 1):
        monitor.report_failure("lcda")
    assert calls == []


def test_no_callback_means_no_restart(monitor) -> None:
    """Threshold can still be tracked for diagnostics; the absence of
    a restart callback is acceptable (some services have no
    automated recovery path)."""
    monitor.register_service("read_only")
    for _ in range(5):
        monitor.report_failure("read_only")
    assert monitor.health("read_only").is_unhealthy


# ----------------------------------------------------------------------
# Backoff between restart attempts
# ----------------------------------------------------------------------
def test_backoff_blocks_immediate_second_restart(monitor, clock) -> None:
    """A restart was just attempted — even if more failures pour in,
    the next callback only fires after the backoff window."""
    calls: list[float] = []
    monitor.register_service("lcda", restart_callback=lambda: calls.append(clock.t))
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        monitor.report_failure("lcda")
    # 1st restart fired immediately at t≈clock_start.
    assert len(calls) == 1
    # More failures while still within initial backoff (1s) — no second restart.
    monitor.report_failure("lcda")
    monitor.report_failure("lcda")
    assert len(calls) == 1
    # Advance past the backoff; next failure triggers the second restart.
    clock.advance(2.0)
    monitor.report_failure("lcda")
    assert len(calls) == 2


def test_backoff_doubles_per_attempt(monitor, clock) -> None:
    """1s, 2s, 4s, 8s (capped at max_backoff)."""
    calls: list[float] = []
    monitor.register_service(
        "lcda", restart_callback=lambda: calls.append(clock.t),
    )

    def burn_to_threshold() -> None:
        for _ in range(DEFAULT_FAILURE_THRESHOLD):
            monitor.report_failure("lcda")

    burn_to_threshold()
    first_call_at = calls[-1]

    # 1s backoff before next eligible restart.
    clock.advance(1.5)
    monitor.report_failure("lcda")
    assert len(calls) == 2
    second_call_at = calls[-1]
    assert second_call_at - first_call_at == pytest.approx(1.5)

    # Now backoff is 2s.
    clock.advance(0.5)
    monitor.report_failure("lcda")
    assert len(calls) == 2  # still backing off
    clock.advance(2.0)
    monitor.report_failure("lcda")
    assert len(calls) == 3


def test_callback_crash_does_not_kill_monitor(monitor) -> None:
    """A buggy restart callback that raises must not prevent further
    failure tracking — the monitor logs and moves on."""
    def boom():
        raise RuntimeError("buggy restart")

    monitor.register_service("lcda", restart_callback=boom)
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        monitor.report_failure("lcda")
    # Subsequent failures still update counters.
    monitor.report_failure("lcda")
    h = monitor.health("lcda")
    assert h.total_failures == DEFAULT_FAILURE_THRESHOLD + 1


# ----------------------------------------------------------------------
# Counter reset after long quiet
# ----------------------------------------------------------------------
def test_consecutive_resets_after_quiet_period(monitor, clock) -> None:
    """A failure followed by 5+ minutes of silence shouldn't accumulate
    into 'unhealthy' on the next sporadic blip."""
    monitor.register_service("lcda")
    monitor.report_failure("lcda")
    assert monitor.health("lcda").consecutive_failures == 1
    clock.advance(COUNTER_RESET_AFTER_S + 1.0)
    monitor.report_failure("lcda")
    assert monitor.health("lcda").consecutive_failures == 1


# ----------------------------------------------------------------------
# Introspection
# ----------------------------------------------------------------------
def test_all_unhealthy_lists_breached_services(monitor) -> None:
    monitor.register_service("a")
    monitor.register_service("b")
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        monitor.report_failure("a")
    monitor.report_failure("b")
    assert monitor.all_unhealthy() == ["a"]


def test_health_returns_defensive_copy(monitor) -> None:
    monitor.register_service("lcda")
    monitor.report_failure("lcda")
    h = monitor.health("lcda")
    h.consecutive_failures = 999  # mutate the copy
    assert monitor.health("lcda").consecutive_failures == 1


def test_format_error_handles_exception_and_string(monitor) -> None:
    monitor.report_failure("a", RuntimeError("X"))
    monitor.report_failure("b", "raw string")
    monitor.report_failure("c", None)
    assert monitor.health("a").last_error == "RuntimeError: X"
    assert monitor.health("b").last_error == "raw string"
    assert monitor.health("c").last_error is None
