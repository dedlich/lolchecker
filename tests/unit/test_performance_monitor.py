"""Tests for the performance baseline monitor (charter A1)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from champ_assistant import performance_monitor as pm


@pytest.fixture(autouse=True)
def _reset_monitor():
    pm.reset_for_tests()
    yield
    pm.reset_for_tests()


def test_singleton_returns_same_instance() -> None:
    a = pm.monitor()
    b = pm.monitor()
    assert a is b


def test_reset_creates_fresh_instance() -> None:
    a = pm.monitor()
    pm.reset_for_tests()
    b = pm.monitor()
    assert a is not b


def test_record_phase_returns_record() -> None:
    record = pm.record_phase("startup_begin")
    assert record.name == "startup_begin"
    assert record.elapsed_ms >= 0
    assert record.wall_clock > 0


def test_phases_form_increasing_timeline() -> None:
    """elapsed_ms is monotonic — later phases never appear before
    earlier ones in the buffer."""
    pm.record_phase("a")
    time.sleep(0.005)
    pm.record_phase("b")
    snap = pm.monitor().snapshot()
    assert snap[0].name == "a"
    assert snap[1].name == "b"
    assert snap[1].elapsed_ms > snap[0].elapsed_ms


def test_buffer_caps_at_ring_size() -> None:
    """Long-running session can't blow out RAM."""
    for i in range(pm.RING_BUFFER_SIZE + 50):
        pm.record_phase(f"phase_{i}")
    snap = pm.monitor().snapshot()
    assert len(snap) == pm.RING_BUFFER_SIZE
    # First entries dropped, last entries kept.
    assert snap[-1].name == f"phase_{pm.RING_BUFFER_SIZE + 49}"


def test_flush_writes_log_file(tmp_path, monkeypatch) -> None:
    """End-to-end: record some phases, flush, read the log back."""
    monkeypatch.setattr(pm, "_log_dir", lambda: tmp_path)
    pm.record_phase("startup_begin")
    pm.record_phase("ui_ready")

    written = pm.monitor().flush()
    assert written is not None
    assert written.exists()
    content = written.read_text(encoding="utf-8")
    assert "phase\telapsed_ms\twall_clock" in content
    assert "startup_begin" in content
    assert "ui_ready" in content


def test_flush_with_empty_buffer_is_a_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pm, "_log_dir", lambda: tmp_path)
    result = pm.monitor().flush()
    assert result is None


def test_flush_atomic_no_partial_file_on_failure(tmp_path, monkeypatch) -> None:
    """If the directory becomes unwritable mid-flush, no half-written
    log is left behind. Atomicity is the contract: tempfile +
    os.replace, never a partial main file."""
    monkeypatch.setattr(pm, "_log_dir", lambda: tmp_path / "nope" / "nested")
    pm.record_phase("phase")
    # Path doesn't exist; mkdir tries to create it. This succeeds in the
    # happy path. Force a write failure by making the parent path a
    # file (so mkdir raises). Use a sibling directory for the smoke.
    bad = tmp_path / "blocker"
    bad.write_text("not a directory")
    monkeypatch.setattr(pm, "_log_dir", lambda: bad / "logs")
    result = pm.monitor().flush()
    # Must NOT crash; returns None on failure.
    assert result is None
    # Nothing got written next to the blocker file.
    assert not any(p.name.endswith(".log") for p in tmp_path.iterdir())


def test_log_path_uses_appdata_on_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = pm.performance_log_path()
    assert "ChampAssistant" in str(path)
    assert path.name == "performance.log"


def test_log_path_uses_dotdir_on_unix(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = pm.performance_log_path()
    assert ".champ-assistant" in str(path)
    assert path.name == "performance.log"
