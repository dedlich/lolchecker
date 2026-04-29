"""Tests for Safe Mode startup detection + clean-shutdown marker."""
from __future__ import annotations

from pathlib import Path

from champ_assistant import safe_mode


def test_no_crash_no_safe_mode(tmp_path: Path) -> None:
    crash = tmp_path / "crash_report.json"
    marker = tmp_path / "clean_shutdown.marker"
    mode = safe_mode.decide_startup_mode(crash_path=crash, marker_path=marker)
    assert mode.safe is False


def test_crash_present_marker_absent_triggers_safe_mode(tmp_path: Path) -> None:
    crash = tmp_path / "crash_report.json"
    marker = tmp_path / "clean_shutdown.marker"
    crash.write_text('{"x":1}')
    mode = safe_mode.decide_startup_mode(crash_path=crash, marker_path=marker)
    assert mode.safe is True
    assert "crash" in mode.reason.lower()


def test_crash_and_marker_both_present_does_not_trigger_safe(tmp_path: Path) -> None:
    """User had a crash, restarted, the prior session shut down clean —
    don't keep nagging in Safe Mode forever."""
    crash = tmp_path / "crash_report.json"
    marker = tmp_path / "clean_shutdown.marker"
    crash.write_text('{"x":1}')
    marker.write_text("")
    mode = safe_mode.decide_startup_mode(crash_path=crash, marker_path=marker)
    assert mode.safe is False


def test_consume_marker_deletes_it(tmp_path: Path) -> None:
    marker = tmp_path / "clean_shutdown.marker"
    marker.write_text("")
    safe_mode.consume_clean_shutdown_marker(marker)
    assert not marker.exists()


def test_consume_marker_silent_when_missing(tmp_path: Path) -> None:
    """Must not raise on a fresh-install / first-run."""
    safe_mode.consume_clean_shutdown_marker(tmp_path / "missing.marker")


def test_write_clean_shutdown_marker_atomic(tmp_path: Path) -> None:
    marker = tmp_path / "clean_shutdown.marker"
    assert safe_mode.write_clean_shutdown_marker(marker) is True
    assert marker.is_file()
    # Empty file content — existence is the signal.
    assert marker.read_text() == ""


def test_write_marker_overwrites_existing(tmp_path: Path) -> None:
    marker = tmp_path / "clean_shutdown.marker"
    marker.write_text("stale junk")
    safe_mode.write_clean_shutdown_marker(marker)
    assert marker.read_text() == ""


def test_resume_normal_clears_crash_and_writes_marker(tmp_path: Path) -> None:
    """End-to-end: simulate a Safe Mode session where the user clicks
    Resume Normal — both files should reach the expected post-state."""
    crash = tmp_path / "crash_report.json"
    marker = tmp_path / "clean_shutdown.marker"
    crash.write_text('{"x":1}')
    safe_mode.resume_normal_mode(crash_path=crash, marker_path=marker)
    assert not crash.exists()
    assert marker.is_file()


def test_full_safe_mode_lifecycle(tmp_path: Path) -> None:
    """Walk a complete Safe Mode life cycle:
       1. Session crashes — crash_report appears, no marker.
       2. Restart — decide_startup_mode == safe.
       3. Resume Normal Mode — crash gone, marker present.
       4. Restart again — decide_startup_mode != safe."""
    crash = tmp_path / "crash_report.json"
    marker = tmp_path / "clean_shutdown.marker"

    # 1. Crash leaves only the report
    crash.write_text('{"x":1}')
    assert safe_mode.decide_startup_mode(crash_path=crash, marker_path=marker).safe is True

    # 2. User clicks Resume Normal
    safe_mode.resume_normal_mode(crash_path=crash, marker_path=marker)

    # 3. Now the next start clears the marker (session boots normal)
    safe_mode.consume_clean_shutdown_marker(marker)
    assert not marker.exists()
    assert not crash.exists()
    assert safe_mode.decide_startup_mode(crash_path=crash, marker_path=marker).safe is False
