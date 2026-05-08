"""Tests for the lightweight UX telemetry layer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from champ_assistant import telemetry
from champ_assistant.telemetry import (
    EV_CONFIDENCE_BAND,
    EV_FIGHT_WINDOW,
    EV_WIDGET_SHOWN,
    TelemetryRecorder,
    _band_label,
    make_band_tracker,
    make_fight_window_detector,
)


@pytest.fixture
def qt_app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fresh_recorder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qt_app):  # type: ignore[no-untyped-def]
    """Singleton-isolated recorder writing to tmp_path so the test
    leaves no telemetry on disk."""
    monkeypatch.setattr(
        telemetry, "telemetry_path", lambda: tmp_path / "telemetry.jsonl",
    )
    telemetry.reset_singleton_for_tests()
    rec = telemetry.recorder()
    yield rec
    rec.stop()
    telemetry.reset_singleton_for_tests()


# ----------------------------------------------------------------------
# Recorder basics
# ----------------------------------------------------------------------
def test_record_appends_to_ring(fresh_recorder) -> None:
    fresh_recorder.record("test_event", {"k": "v"})
    recent = fresh_recorder.recent()
    assert len(recent) == 1
    assert recent[0]["event"] == "test_event"
    assert recent[0]["payload"] == {"k": "v"}
    assert "ts" in recent[0]


def test_record_without_payload_emits_empty_dict(fresh_recorder) -> None:
    fresh_recorder.record("e")
    assert fresh_recorder.recent()[0]["payload"] == {}


def test_ring_size_is_bounded() -> None:
    rec = TelemetryRecorder(ring_size=5)
    for i in range(20):
        rec.record("e", {"i": i})
    recent = rec.recent()
    assert len(recent) == 5
    # Oldest dropped — only the last 5 indices remain.
    assert [e["payload"]["i"] for e in recent] == [15, 16, 17, 18, 19]


def test_flush_writes_jsonl_to_disk(tmp_path: Path, qt_app) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "t.jsonl"
    rec = TelemetryRecorder(path=path)
    rec.record("a", {"x": 1})
    rec.record("b", {"x": 2})
    rec._flush()  # explicit; tests don't wait for QTimer
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event"] == "a"
    assert parsed[1]["payload"]["x"] == 2


def test_flush_is_idempotent_when_no_pending(tmp_path: Path, qt_app) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "t.jsonl"
    rec = TelemetryRecorder(path=path)
    rec._flush()  # nothing to flush
    rec._flush()  # still nothing
    assert not path.exists()


def test_record_after_stop_does_not_grow_pending(tmp_path: Path, qt_app) -> None:  # type: ignore[no-untyped-def]
    """v1.10.95: stopping telemetry must short-circuit ``_pending``
    appends. Otherwise long sessions where the user disabled telemetry
    via Settings would accumulate unbounded events in memory until
    process exit (the timer is stopped, so they never flush)."""
    path = tmp_path / "t.jsonl"
    rec = TelemetryRecorder(path=path)
    rec.start()
    rec.record("before_stop", {"x": 1})
    rec.stop()
    # Final flush has already written the pre-stop event to disk and
    # cleared _pending. From here, every record() must be discarded
    # from the pending queue (ring still accepts it for debugging).
    for i in range(50):
        rec.record("after_stop", {"i": i})
    assert rec._pending == [], (
        "telemetry pending queue grew after stop() — memory leak"
    )
    # Ring still has the disabled-period entries (bounded by maxlen).
    recent = rec.recent()
    assert any(e["event"] == "after_stop" for e in recent)


def test_start_after_stop_resumes_pending_appends(tmp_path: Path, qt_app) -> None:  # type: ignore[no-untyped-def]
    """Re-enabling telemetry must restore the pending-queue path so
    new events flush to disk on the next interval. Events recorded
    during the disabled window stay dropped — that matches the user's
    intent when they unchecked telemetry."""
    path = tmp_path / "t.jsonl"
    rec = TelemetryRecorder(path=path)
    rec.start()
    rec.stop()
    rec.record("during_off", {"x": 1})
    rec.start()
    rec.record("after_on", {"y": 2})
    assert any(e["event"] == "after_on" for e in rec._pending)
    assert not any(e["event"] == "during_off" for e in rec._pending), (
        "events recorded while telemetry was off must not flush "
        "after re-enable"
    )


def test_rotation_at_size_cap(tmp_path: Path, qt_app) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "t.jsonl"
    rec = TelemetryRecorder(path=path, max_file_bytes=100)
    # First batch fits.
    for i in range(3):
        rec.record("e", {"i": i})
    rec._flush()
    assert path.is_file()
    # Force the file over the cap, then flush again — should rotate.
    path.write_text("X" * 200)
    rec.record("after", {})
    rec._flush()
    assert path.with_suffix(".jsonl.1").is_file()
    # New file contains only the post-rotation event.
    assert "after" in path.read_text()


# ----------------------------------------------------------------------
# Band tracker
# ----------------------------------------------------------------------
def test_band_label_thresholds() -> None:
    assert _band_label(1.0) == "HIGH"
    assert _band_label(0.8) == "HIGH"
    assert _band_label(0.79) == "MID"
    assert _band_label(0.4) == "MID"
    assert _band_label(0.39) == "LOW"
    assert _band_label(0.0) == "LOW"


def test_band_tracker_emits_only_on_transition(fresh_recorder, qt_app) -> None:  # type: ignore[no-untyped-def]
    tracker = make_band_tracker()
    from champ_assistant.jungle_timeline import CampState

    def states(conf: float) -> dict[str, CampState]:
        return {
            "red": CampState("red", "Red Buff", "respawning", 90, 60, conf),
        }

    tracker(states(0.95))   # initial — sets baseline, no event
    tracker(states(0.9))    # still HIGH — silent
    tracker(states(0.7))    # HIGH→MID — emit
    tracker(states(0.6))    # still MID — silent
    tracker(states(0.3))    # MID→LOW — emit
    tracker(states(0.95))   # LOW→HIGH — emit (e.g. new game reset)

    band_events = [e for e in fresh_recorder.recent() if e["event"] == EV_CONFIDENCE_BAND]
    assert len(band_events) == 3
    assert band_events[0]["payload"] == {"camp_id": "red", "from": "HIGH", "to": "MID"}
    assert band_events[1]["payload"] == {"camp_id": "red", "from": "MID", "to": "LOW"}
    assert band_events[2]["payload"] == {"camp_id": "red", "from": "LOW", "to": "HIGH"}


# ----------------------------------------------------------------------
# Fight-window detector
# ----------------------------------------------------------------------
def test_fight_window_triggers_on_objective_kill(fresh_recorder, qt_app) -> None:  # type: ignore[no-untyped-def]
    detect = make_fight_window_detector()
    detect([{"EventID": 1, "EventName": "DragonKill"}])
    events = [e for e in fresh_recorder.recent() if e["event"] == EV_FIGHT_WINDOW]
    assert len(events) == 1
    assert events[0]["payload"]["active"] is True


def test_fight_window_triggers_on_event_burst(fresh_recorder, qt_app) -> None:  # type: ignore[no-untyped-def]
    detect = make_fight_window_detector(threshold=3, window_s=10.0)
    # Three non-objective events arriving in the same tick — bursts the
    # threshold immediately.
    detect([
        {"EventID": 1, "EventName": "ChampionKill"},
        {"EventID": 2, "EventName": "ChampionKill"},
        {"EventID": 3, "EventName": "ChampionKill"},
    ])
    events = [e for e in fresh_recorder.recent() if e["event"] == EV_FIGHT_WINDOW]
    assert len(events) == 1
    assert events[0]["payload"]["active"] is True


def test_fight_window_edge_triggered(fresh_recorder, qt_app) -> None:  # type: ignore[no-untyped-def]
    """Subsequent ticks while still in fight must not re-emit. Only
    the transition should fire."""
    detect = make_fight_window_detector(threshold=3, window_s=10.0)
    detect([
        {"EventID": 1, "EventName": "ChampionKill"},
        {"EventID": 2, "EventName": "ChampionKill"},
        {"EventID": 3, "EventName": "ChampionKill"},
    ])
    detect([{"EventID": 4, "EventName": "ChampionKill"}])  # still in fight
    detect([])  # no new events, still in window
    events = [e for e in fresh_recorder.recent() if e["event"] == EV_FIGHT_WINDOW]
    # Only the first transition emitted.
    assert len(events) == 1


def test_fight_window_dedup_event_ids(fresh_recorder, qt_app) -> None:  # type: ignore[no-untyped-def]
    """LCDA returns the cumulative event log on every poll — repeated
    EventIDs must not pile up the threshold count."""
    detect = make_fight_window_detector(threshold=3, window_s=10.0)
    same_payload = [
        {"EventID": 1, "EventName": "ChampionKill"},
        {"EventID": 2, "EventName": "ChampionKill"},
    ]
    detect(same_payload)  # 2 unique events — under threshold
    detect(same_payload)  # same IDs again — must NOT push count to 4
    detect(same_payload)  # still 2 unique — no fight
    events = [e for e in fresh_recorder.recent() if e["event"] == EV_FIGHT_WINDOW]
    assert events == []


# ----------------------------------------------------------------------
# Singleton
# ----------------------------------------------------------------------
def test_recorder_singleton_returns_same_instance(qt_app) -> None:  # type: ignore[no-untyped-def]
    telemetry.reset_singleton_for_tests()
    a = telemetry.recorder()
    b = telemetry.recorder()
    assert a is b
    telemetry.reset_singleton_for_tests()


def test_record_handles_unserializable_safely(fresh_recorder) -> None:
    """Telemetry must NEVER crash the UI. A bad payload may fail at
    flush-time silently but record() itself must never raise."""
    fresh_recorder.record("e", {"obj": object()})  # not JSON-serializable
    # Recent should still contain the event (record itself doesn't
    # serialize).
    assert any(e["event"] == "e" for e in fresh_recorder.recent())
    # Flush should warn + drop the batch, not crash.
    fresh_recorder._flush()
