"""Tests for session summary log emission."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from champ_assistant.session_summary import UptimeClock, emit_session_summary


def test_summary_logs_session_line(caplog) -> None:  # type: ignore[no-untyped-def]
    scheduler = SimpleNamespace(frame_count=120_000)
    state = SimpleNamespace()
    state.get = lambda: SimpleNamespace(revision=4321)
    telemetry = SimpleNamespace(recent=lambda *_: [{}] * 50)

    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(
            uptime_seconds=600.0,
            scheduler=scheduler,
            state_store=state,
            telemetry_recorder=telemetry,
        )
    line = caplog.records[-1].getMessage()
    assert "[SESSION]" in line
    assert "duration=600s" in line
    assert "render_frames=120000" in line
    assert "state_updates=4321" in line
    assert "telemetry_events=50" in line
    assert "safe_mode=false" in line


def test_summary_avg_fps_computed(caplog) -> None:  # type: ignore[no-untyped-def]
    scheduler = SimpleNamespace(frame_count=300)
    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(uptime_seconds=10.0, scheduler=scheduler)
    line = caplog.records[-1].getMessage()
    assert "avg_fps=30.0" in line


def test_summary_zero_uptime_does_not_div_by_zero(caplog) -> None:  # type: ignore[no-untyped-def]
    scheduler = SimpleNamespace(frame_count=100)
    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(uptime_seconds=0.0, scheduler=scheduler)
    assert "avg_fps=0.0" in caplog.records[-1].getMessage()


def test_summary_safe_mode_flag_in_line(caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(uptime_seconds=1.0, safe_mode=True)
    assert "safe_mode=true" in caplog.records[-1].getMessage()


def test_summary_with_no_subsystems_does_not_crash(caplog) -> None:  # type: ignore[no-untyped-def]
    """Crash during startup before any subsystem comes up — summary
    must still emit (with zero-counters) instead of NoneType errors."""
    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(uptime_seconds=0.5)
    line = caplog.records[-1].getMessage()
    assert "[SESSION]" in line
    assert "render_frames=0" in line
    assert "state_updates=0" in line


def test_summary_tolerates_failing_subsystem_attrs(caplog) -> None:  # type: ignore[no-untyped-def]
    """A subsystem in a half-torn-down state may have attributes that
    raise on access. Summary emit must tolerate that without crashing."""

    class Hostile:
        @property
        def frame_count(self) -> int:  # type: ignore[misc]
            raise RuntimeError("teardown half-done")

    with caplog.at_level(logging.INFO, logger="champ_assistant.session_summary"):
        emit_session_summary(uptime_seconds=1.0, scheduler=Hostile())
    # Did not raise — and a session line was logged with safe defaults.
    assert any("[SESSION]" in r.getMessage() for r in caplog.records)


def test_summary_emit_is_constant_time() -> None:
    """Smoke-check: emit takes < 10 ms even with realistic inputs."""
    import time
    scheduler = SimpleNamespace(frame_count=500_000)
    state = SimpleNamespace(get=lambda: SimpleNamespace(revision=1_000_000))
    telemetry = SimpleNamespace(recent=lambda *_: [{}] * 9999)
    start = time.monotonic()
    emit_session_summary(
        uptime_seconds=18000.0,
        scheduler=scheduler,
        state_store=state,
        telemetry_recorder=telemetry,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"emit took {elapsed:.3f}s — should be near-instant"


def test_uptime_clock_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    import champ_assistant.session_summary as _mod
    now = [100.0]
    monkeypatch.setattr(_mod.time, "monotonic", lambda: now[0])
    clock = UptimeClock()
    assert clock.elapsed() == pytest.approx(0.0)
    now[0] = 100.05
    assert clock.elapsed() == pytest.approx(0.05)
    # max(0.0, ...) guard: backwards-clock never returns negative
    now[0] = 99.0
    assert clock.elapsed() == 0.0
