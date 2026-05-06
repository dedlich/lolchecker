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


# ---------------------------------------------------------------------------
# RuleTimingRecorder — Strategy A2 instrumentation
# ---------------------------------------------------------------------------

def test_rule_timing_recorder_records_samples() -> None:
    rec = pm.RuleTimingRecorder()
    rec.record("rule_a", 1.5)
    rec.record("rule_a", 2.5)
    rec.record("rule_b", 0.1)
    snap = rec.snapshot()
    assert snap["rule_a"] == [1.5, 2.5]
    assert snap["rule_b"] == [0.1]


def test_rule_timing_recorder_ring_buffer_caps_old_samples() -> None:
    rec = pm.RuleTimingRecorder()
    # Push more than RULE_TIMING_RING_SIZE samples; oldest get evicted.
    cap = pm.RULE_TIMING_RING_SIZE
    for i in range(cap + 50):
        rec.record("hot_rule", float(i))
    samples = rec.snapshot()["hot_rule"]
    assert len(samples) == cap
    # Oldest (0..49) should be gone; newest (cap+49) should be in.
    assert min(samples) == 50.0
    assert max(samples) == float(cap + 49)


def test_rule_timing_digest_omits_empty_buckets() -> None:
    rec = pm.RuleTimingRecorder()
    rec.record("rule_a", 1.0)
    digest = rec.digest()
    names = {row[0] for row in digest}
    assert "rule_a" in names
    assert "rule_b" not in names


def test_rule_timing_digest_computes_percentiles() -> None:
    rec = pm.RuleTimingRecorder()
    # Known distribution: 1..10 ms — 10 invocations, none "fired".
    for ms in range(1, 11):
        rec.record("known", float(ms))
    digest = {row[0]: row for row in rec.digest()}
    name, inv, fires, fire_rate, p50, p95, mx, mean = digest["known"]
    assert inv == 10
    assert fires == 0
    assert fire_rate == 0.0
    assert mx == 10.0
    assert mean == 5.5
    # p50 nearest-rank with 10 samples: index 5 → value 6.0 (sort-ascending).
    assert p50 == 6.0
    # p95 nearest-rank with 10 samples: index 9 → value 10.0.
    assert p95 == 10.0


def test_rule_timing_digest_sorts_by_p95_descending() -> None:
    rec = pm.RuleTimingRecorder()
    for _ in range(20):
        rec.record("fast", 0.1)
        rec.record("slow", 5.0)
        rec.record("medium", 1.0)
    digest = rec.digest()
    names = [row[0] for row in digest]
    assert names == ["slow", "medium", "fast"]


def test_rule_timing_recorder_singleton_is_stable() -> None:
    pm.reset_for_tests()
    a = pm.rule_timing_recorder()
    b = pm.rule_timing_recorder()
    assert a is b


def test_rule_timing_flush_writes_tsv(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pm, "_log_dir", lambda: tmp_path)
    rec = pm.RuleTimingRecorder()
    rec.record("rule_x", 0.5)
    rec.record("rule_x", 1.5)
    rec.record("rule_y", 0.1)
    out = rec.flush()
    assert out is not None
    assert out.name == "rule_timing.log"
    content = out.read_text(encoding="utf-8").strip().splitlines()
    # Header + 2 rules.
    assert content[0] == (
        "rule\tinvocations\tfires\tfire_rate"
        "\tp50_ms\tp95_ms\tmax_ms\tmean_ms"
    )
    assert len(content) == 3
    # One row mentions rule_x, the other rule_y.
    assert any("rule_x" in line for line in content[1:])
    assert any("rule_y" in line for line in content[1:])


def test_rule_timing_flush_returns_none_when_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pm, "_log_dir", lambda: tmp_path)
    rec = pm.RuleTimingRecorder()
    assert rec.flush() is None


def test_rule_timing_flush_path_failure_returns_none(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    monkeypatch.setattr(pm, "_log_dir", lambda: blocker / "logs")
    rec = pm.RuleTimingRecorder()
    rec.record("rule_a", 1.0)
    assert rec.flush() is None


def test_rule_timing_reset_for_tests_drops_singleton() -> None:
    a = pm.rule_timing_recorder()
    a.record("rule_z", 1.0)
    pm.reset_for_tests()
    b = pm.rule_timing_recorder()
    assert b is not a
    assert "rule_z" not in b.snapshot()


# ---------------------------------------------------------------------------
# Activation tracking — fired counter + fire rate
# ---------------------------------------------------------------------------

def test_record_default_does_not_count_as_fire() -> None:
    """``record(name, ms)`` without ``fired=True`` is an invocation but not a fire."""
    rec = pm.RuleTimingRecorder()
    rec.record("rule_a", 1.0)
    rec.record("rule_a", 1.0)
    inv, fires = rec.activation_snapshot()
    assert inv["rule_a"] == 2
    assert fires.get("rule_a", 0) == 0


def test_record_with_fired_flag_increments_fire_count() -> None:
    rec = pm.RuleTimingRecorder()
    rec.record("rule_a", 1.0, fired=True)
    rec.record("rule_a", 1.0, fired=False)
    rec.record("rule_a", 1.0, fired=True)
    inv, fires = rec.activation_snapshot()
    assert inv["rule_a"] == 3
    assert fires["rule_a"] == 2


def test_digest_includes_fire_rate() -> None:
    rec = pm.RuleTimingRecorder()
    # 4 invocations, 1 fire → fire_rate 0.25
    rec.record("rule_a", 1.0, fired=True)
    rec.record("rule_a", 1.0)
    rec.record("rule_a", 1.0)
    rec.record("rule_a", 1.0)
    digest = {row[0]: row for row in rec.digest()}
    name, inv, fires, fire_rate, *_ = digest["rule_a"]
    assert inv == 4
    assert fires == 1
    assert fire_rate == 0.25


def test_digest_fire_rate_zero_when_never_fired() -> None:
    rec = pm.RuleTimingRecorder()
    for _ in range(5):
        rec.record("never_fires", 0.5)
    digest = {row[0]: row for row in rec.digest()}
    _, _, fires, fire_rate, *_ = digest["never_fires"]
    assert fires == 0
    assert fire_rate == 0.0


def test_digest_fire_rate_one_when_always_fires() -> None:
    rec = pm.RuleTimingRecorder()
    for _ in range(5):
        rec.record("always_fires", 0.5, fired=True)
    digest = {row[0]: row for row in rec.digest()}
    _, _, fires, fire_rate, *_ = digest["always_fires"]
    assert fires == 5
    assert fire_rate == 1.0


def test_invocation_count_survives_ring_buffer_eviction() -> None:
    """Lifetime invocation counter doesn't decay with the ring buffer.
    A long session with cap+10 calls should still report cap+10 invocations
    (not just the cap most recent samples)."""
    rec = pm.RuleTimingRecorder()
    cap = pm.RULE_TIMING_RING_SIZE
    for _ in range(cap + 10):
        rec.record("hot_rule", 0.1, fired=True)
    inv, fires = rec.activation_snapshot()
    # Lifetime count is total calls, not just retained samples.
    assert inv["hot_rule"] == cap + 10
    assert fires["hot_rule"] == cap + 10
    # But the duration ring buffer is capped.
    assert len(rec.snapshot()["hot_rule"]) == cap


def test_evaluate_records_activation_for_firing_rules() -> None:
    """End-to-end: evaluate() should mark a rule as fired when it returns
    a Recommendation. Easiest way to verify: stub a snapshot that triggers
    rule_first_blood and check the recorder."""
    from dataclasses import dataclass, field
    from champ_assistant.advisor.decision_engine import (
        evaluate, reset_first_blood_hysteresis,
    )

    pm.reset_for_tests()
    reset_first_blood_hysteresis()

    @dataclass
    class _P:
        summoner_name: str = ""
        champion_name: str = ""

    @dataclass
    class _Snap:
        game_time: float = 200.0
        raw_events: list = field(default_factory=list)
        enemies: list = field(default_factory=list)
        allies: list = field(default_factory=list)
        ally_aggregate: object = None
        enemy_aggregate: object = None
        objectives: list = field(default_factory=list)
        active_team: str = "ORDER"
        active_summoner: str = "Me"
        active_level: int = 3
        active_items: int = 0
        new_spikes: list = field(default_factory=list)
        enemy_spikes: list = field(default_factory=list)
        gank_alert: object = None
        tilt_state: object = None
        active_combat: object = None
        lane_opponent_alert: object = None
        game_result: str = ""
        game_mode: str = ""

    snap = _Snap(
        game_time=200.0,
        raw_events=[{
            "EventName": "ChampionKill", "EventTime": 180.0,
            "KillerName": "Me", "VictimName": "Yasuo", "Assisters": [],
        }],
        allies=[_P(summoner_name="Me", champion_name="MyChamp")],
        enemies=[_P(summoner_name="Yasuo", champion_name="Yasuo")],
    )
    evaluate(snap)
    inv, fires = pm.rule_timing_recorder().activation_snapshot()
    # rule_first_blood should have fired exactly once on this snapshot.
    assert inv.get("rule_first_blood", 0) == 1
    assert fires.get("rule_first_blood", 0) == 1
    # rule_late_game_group ran but should NOT have fired (game time too early).
    assert inv.get("rule_late_game_group", 0) == 1
    assert fires.get("rule_late_game_group", 0) == 0
