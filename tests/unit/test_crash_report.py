"""Tests for crash report persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant import crash_report


def _raise_and_capture(exc: type[BaseException], msg: str = "boom"):
    """Build a real (exc_type, exc_value, exc_tb) triple for tests —
    raise + catch so the traceback object is genuine."""
    try:
        raise exc(msg)
    except exc as e:  # type: ignore[misc]
        return type(e), e, e.__traceback__


def test_write_creates_valid_json(tmp_path: Path) -> None:
    target = tmp_path / "crash_report.json"
    et, ev, tb = _raise_and_capture(RuntimeError, "kaboom")
    written = crash_report.write_crash_report(
        et, ev, tb, version="1.0.0", uptime_seconds=42.5, path=target,
    )
    assert written == target
    data = json.loads(target.read_text())
    assert data["version"] == "1.0.0"
    assert data["uptime_seconds"] == 42.5
    assert data["exception"]["type"] == "RuntimeError"
    assert data["exception"]["message"] == "kaboom"
    assert "kaboom" in data["exception"]["traceback"]


def test_write_overwrites_previous(tmp_path: Path) -> None:
    target = tmp_path / "crash_report.json"
    target.write_text('{"old": true}')
    et, ev, tb = _raise_and_capture(ValueError, "new")
    crash_report.write_crash_report(et, ev, tb, version="1.0.0", path=target)
    data = json.loads(target.read_text())
    assert "old" not in data
    assert data["exception"]["type"] == "ValueError"


def test_write_is_atomic_no_partial_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Simulate a crash mid-write by making os.replace raise. The
    target file must NOT be partially written — either intact or
    absent."""
    target = tmp_path / "crash_report.json"
    target.write_text('{"prior": "intact"}')
    et, ev, tb = _raise_and_capture(RuntimeError, "x")

    import os
    original_replace = os.replace
    def _failing_replace(*args, **kwargs):
        raise OSError("simulated failure during atomic rename")
    monkeypatch.setattr(os, "replace", _failing_replace)

    result = crash_report.write_crash_report(et, ev, tb, version="1.0.0", path=target)
    assert result is None
    # Prior content untouched — atomic write guarantees this.
    assert json.loads(target.read_text()) == {"prior": "intact"}
    # Restore for cleanup
    monkeypatch.setattr(os, "replace", original_replace)


def test_state_collector_partial_data_tolerated(tmp_path: Path) -> None:
    """Collector returns only some fields → others fall back to safe
    defaults, write succeeds."""
    target = tmp_path / "crash_report.json"
    et, ev, tb = _raise_and_capture(RuntimeError, "x")

    def _partial() -> dict:
        return {"phase": "in_game"}  # connection_state, widgets etc. missing

    crash_report.write_crash_report(
        et, ev, tb, version="1.0.0", path=target,
        state_collector=_partial,
    )
    data = json.loads(target.read_text())
    assert data["phase"] == "in_game"
    assert data["connection_state"] == ""
    assert data["active_widgets"] == []


def test_state_collector_raising_does_not_break_write(tmp_path: Path) -> None:
    """A buggy collector (raises during a real crash) must not block
    the report write — empty state is logged + file still written."""
    target = tmp_path / "crash_report.json"
    et, ev, tb = _raise_and_capture(RuntimeError, "x")

    def _broken() -> dict:
        raise RuntimeError("collector itself crashed")

    written = crash_report.write_crash_report(
        et, ev, tb, version="1.0.0", path=target, state_collector=_broken,
    )
    assert written == target
    data = json.loads(target.read_text())
    assert data["phase"] == ""


def test_traceback_truncated_under_size_cap(tmp_path: Path) -> None:
    """A pathologically long traceback must not blow past the 32 KB
    file cap. Synthesize via a deeply-recursive fake message."""
    target = tmp_path / "crash_report.json"
    huge_message = "x" * 200_000  # 200 KB
    et, ev, tb = _raise_and_capture(RuntimeError, huge_message)
    crash_report.write_crash_report(et, ev, tb, version="1.0.0", path=target)
    size = target.stat().st_size
    assert size <= crash_report.MAX_FILE_BYTES, (
        f"crash report exceeded cap: {size} > {crash_report.MAX_FILE_BYTES}"
    )


def test_has_crash_report(tmp_path: Path) -> None:
    target = tmp_path / "crash_report.json"
    assert crash_report.has_crash_report(target) is False
    target.write_text('{"x": 1}')
    assert crash_report.has_crash_report(target) is True


def test_clear_crash_report_removes_file(tmp_path: Path) -> None:
    target = tmp_path / "crash_report.json"
    target.write_text('{"x": 1}')
    crash_report.clear_crash_report(target)
    assert not target.exists()


def test_clear_crash_report_silent_when_missing(tmp_path: Path) -> None:
    """Calling clear on a missing file must not raise — covers the
    "first launch ever" case."""
    crash_report.clear_crash_report(tmp_path / "does_not_exist.json")


def test_read_crash_report_returns_none_on_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "crash_report.json"
    target.write_text("{ this is not json")
    assert crash_report.read_crash_report(target) is None


def test_write_with_keyboard_interrupt_still_writes(tmp_path: Path) -> None:
    """KeyboardInterrupt is BaseException, not Exception — the writer
    must handle it identically to other exceptions (not the safety.py
    KI carve-out, which only applies to the excepthook itself)."""
    target = tmp_path / "crash_report.json"
    et, ev, tb = _raise_and_capture(KeyboardInterrupt, "user pressed ctrl-c")  # type: ignore[arg-type]
    crash_report.write_crash_report(et, ev, tb, version="1.0.0", path=target)
    data = json.loads(target.read_text())
    assert data["exception"]["type"] == "KeyboardInterrupt"
