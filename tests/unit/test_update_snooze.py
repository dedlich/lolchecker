"""Tests for update notification snooze persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant import update_snooze


@pytest.fixture
def tmp_snooze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    p = tmp_path / "update_snooze.json"
    monkeypatch.setattr(update_snooze, "state_path", lambda: p)
    yield p


# ----------------------------------------------------------------------
# is_active_for: tag-specific gating
# ----------------------------------------------------------------------
def test_empty_snooze_is_never_active() -> None:
    state = update_snooze.SnoozeState()
    assert state.is_active_for("v1.0.0") is False


def test_snooze_active_for_same_tag_within_window() -> None:
    state = update_snooze.SnoozeState(tag="v1.2.3", until_ts=1_000_000.0)
    assert state.is_active_for("v1.2.3", now=999_999.0) is True


def test_snooze_inactive_after_expiry() -> None:
    state = update_snooze.SnoozeState(tag="v1.2.3", until_ts=1_000_000.0)
    assert state.is_active_for("v1.2.3", now=1_000_001.0) is False


def test_snooze_never_blocks_a_different_tag() -> None:
    """Critical: a strictly-newer release must always surface even if
    the user snoozed an earlier tag."""
    state = update_snooze.SnoozeState(tag="v1.2.3", until_ts=10_000_000.0)
    assert state.is_active_for("v1.3.0", now=999_999.0) is False


def test_snooze_inactive_for_empty_tag() -> None:
    state = update_snooze.SnoozeState(tag="v1.2.3", until_ts=10_000_000.0)
    assert state.is_active_for("", now=0) is False


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
def test_load_returns_empty_when_file_missing(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    state = update_snooze.load()
    assert state.tag == ""
    assert state.until_ts == 0.0


def test_snooze_then_load_roundtrip(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    update_snooze.snooze_tag("v0.13.0", duration_s=3600)
    loaded = update_snooze.load()
    assert loaded.tag == "v0.13.0"
    assert loaded.until_ts > 0
    assert tmp_snooze.is_file()


def test_snooze_writes_json_with_expected_keys(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    update_snooze.snooze_tag("v1.0.1", duration_s=3600)
    data = json.loads(tmp_snooze.read_text())
    assert data["tag"] == "v1.0.1"
    assert isinstance(data["until_ts"], (int, float))


def test_corrupt_file_falls_back_to_empty(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    tmp_snooze.write_text("{ this is not json")
    state = update_snooze.load()
    assert state.tag == ""


def test_clear_removes_file(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    update_snooze.snooze_tag("v1.0.0", duration_s=60)
    assert tmp_snooze.is_file()
    update_snooze.clear()
    assert not tmp_snooze.is_file()


def test_clear_when_no_file_is_silent(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    # Must not raise even if there's nothing to clear.
    update_snooze.clear()


def test_empty_tag_is_not_persisted(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    update_snooze.snooze_tag("")
    assert not tmp_snooze.is_file()


def test_minimum_snooze_duration_enforced(tmp_snooze) -> None:  # type: ignore[no-untyped-def]
    """Anything below 60s is clamped — protects against a bug elsewhere
    accidentally creating a 'snooze' that expires in milliseconds."""
    import time
    before = time.time()
    update_snooze.snooze_tag("v1.0.0", duration_s=1)  # too short
    state = update_snooze.load()
    assert state.until_ts >= before + 60.0
