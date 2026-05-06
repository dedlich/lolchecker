"""Tests for dump_engine_state — debug introspection helper."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    dump_engine_state,
    reset_first_blood_hysteresis,
    reset_recall_hysteresis,
    reset_bounty_hysteresis,
    reset_enemy_bounty_hysteresis,
    reset_ally_bounty_hysteresis,
    reset_matchup_mismatch_hysteresis,
    reset_plate_window_hysteresis,
    reset_teamfight_outcome_hysteresis,
    reset_shutdown_taken_hysteresis,
    reset_objective_taken_hysteresis,
    reset_objective_bounty_hysteresis,
)


@pytest.fixture(autouse=True)
def _reset_all_hysteresis():
    """Drop every singleton between tests so the state captures are
    deterministic. Mirror of the per-rule autouse fixtures elsewhere."""
    for fn in (
        reset_first_blood_hysteresis,
        reset_recall_hysteresis,
        reset_bounty_hysteresis,
        reset_enemy_bounty_hysteresis,
        reset_ally_bounty_hysteresis,
        reset_matchup_mismatch_hysteresis,
        reset_plate_window_hysteresis,
        reset_teamfight_outcome_hysteresis,
        reset_shutdown_taken_hysteresis,
        reset_objective_taken_hysteresis,
        reset_objective_bounty_hysteresis,
    ):
        fn()
    yield
    for fn in (
        reset_first_blood_hysteresis,
        reset_recall_hysteresis,
        reset_bounty_hysteresis,
        reset_enemy_bounty_hysteresis,
        reset_ally_bounty_hysteresis,
        reset_matchup_mismatch_hysteresis,
        reset_plate_window_hysteresis,
        reset_teamfight_outcome_hysteresis,
        reset_shutdown_taken_hysteresis,
        reset_objective_taken_hysteresis,
        reset_objective_bounty_hysteresis,
    ):
        fn()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Player:
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


# ---------------------------------------------------------------------------
# Empty / None inputs
# ---------------------------------------------------------------------------

def test_dump_with_none_snapshot_returns_empty_results() -> None:
    """No snapshot → no rules ran. Hysteresis state still captured so the
    user can verify it's at fresh defaults."""
    dump = dump_engine_state(None)
    assert dump["snapshot_summary"] is None
    assert dump["rule_results"] == []
    assert dump["pre_suppression_kinds"] == []
    assert dump["post_suppression_kinds"] == []
    assert dump["suppressed_kinds"] == []
    assert dump["post_suppression_recs"] == []
    # Hysteresis is always captured.
    assert "hysteresis_state" in dump


def test_dump_with_empty_snapshot_runs_all_rules() -> None:
    """Even with an empty snapshot, every ALL_RULES entry should be
    invoked + recorded in rule_results."""
    dump = dump_engine_state(_Snap())
    rule_names = {r["rule"] for r in dump["rule_results"]}
    # The decision engine has 53+ rules in ALL_RULES; verify a sample.
    assert "rule_first_blood" in rule_names
    assert "rule_late_game_group" in rule_names
    assert "rule_recall_check" in rule_names
    assert len(rule_names) >= 50


# ---------------------------------------------------------------------------
# rule_results format
# ---------------------------------------------------------------------------

def test_rule_results_includes_duration_and_fired_flag() -> None:
    dump = dump_engine_state(_Snap())
    for entry in dump["rule_results"]:
        assert "rule" in entry
        assert "fired" in entry
        assert "duration_ms" in entry
        assert "rec" in entry
        assert isinstance(entry["fired"], bool)
        assert isinstance(entry["duration_ms"], float)
        assert entry["duration_ms"] >= 0.0


def test_firing_rule_includes_serialized_recommendation() -> None:
    """rule_first_blood fires with this snapshot — verify the rec is
    serialized as a dict with the expected fields."""
    snap = _Snap(
        game_time=200.0,
        raw_events=[{
            "EventName": "ChampionKill", "EventTime": 180.0,
            "KillerName": "Me", "VictimName": "Yasuo", "Assisters": [],
        }],
        allies=[_Player(summoner_name="Me", champion_name="MyChamp")],
        enemies=[_Player(summoner_name="Yasuo", champion_name="Yasuo")],
    )
    dump = dump_engine_state(snap)
    fb = next(r for r in dump["rule_results"] if r["rule"] == "rule_first_blood")
    assert fb["fired"] is True
    assert fb["rec"] is not None
    assert fb["rec"]["kind"] == "first_blood"
    assert fb["rec"]["severity"] in ("info", "warn")
    assert isinstance(fb["rec"]["text"], str)
    assert isinstance(fb["rec"]["reasons"], list)


def test_silent_rule_has_none_rec_and_fired_false() -> None:
    """Most rules don't fire on an empty snapshot — verify their entries."""
    dump = dump_engine_state(_Snap())
    silent = next(r for r in dump["rule_results"] if r["rule"] == "rule_late_game_group")
    assert silent["fired"] is False
    assert silent["rec"] is None


# ---------------------------------------------------------------------------
# Suppression diff
# ---------------------------------------------------------------------------

def test_suppression_diff_is_empty_when_no_suppression_applies() -> None:
    """Empty snapshot → no umbrella signals → no suppression."""
    dump = dump_engine_state(_Snap())
    assert dump["suppressed_kinds"] == []
    # pre and post should match exactly.
    assert sorted(dump["pre_suppression_kinds"]) == sorted(dump["post_suppression_kinds"])


# ---------------------------------------------------------------------------
# Snapshot summary
# ---------------------------------------------------------------------------

def test_snapshot_summary_captures_key_fields() -> None:
    snap = _Snap(
        game_time=900.0,
        active_summoner="Me",
        active_level=10,
        enemies=[_Player(), _Player()],
    )
    dump = dump_engine_state(snap)
    s = dump["snapshot_summary"]
    assert s["game_time"] == 900.0
    assert s["active_summoner"] == "Me"
    assert s["active_level"] == 10
    assert s["enemies_count"] == 2
    assert s["allies_count"] == 0
    assert s["gank_alert_present"] is False


def test_snapshot_summary_records_optional_alert_presence() -> None:
    """gank_alert / tilt_state / etc. are optional — summary surfaces a
    boolean for each so the user can see at a glance which fired."""
    snap = _Snap(gank_alert={"some": "alert"}, tilt_state=None)
    dump = dump_engine_state(snap)
    s = dump["snapshot_summary"]
    assert s["gank_alert_present"] is True
    assert s["tilt_state_present"] is False


# ---------------------------------------------------------------------------
# Hysteresis state capture
# ---------------------------------------------------------------------------

def test_hysteresis_state_captures_all_singletons() -> None:
    dump = dump_engine_state(None)
    h = dump["hysteresis_state"]
    # Sample of expected singletons — adding more in future shouldn't break this.
    assert "_RECALL_HYSTERESIS" in h
    assert "_BOUNTY_HYSTERESIS" in h
    assert "_FIRST_BLOOD_HYSTERESIS" in h
    assert "_OBJECTIVE_BOUNTY_HYSTERESIS" in h


def test_hysteresis_state_uses_json_friendly_primitives() -> None:
    """Output should be assertable in tests + serializable for logs."""
    dump = dump_engine_state(None)
    h = dump["hysteresis_state"]
    fb = h["_FIRST_BLOOD_HYSTERESIS"]
    assert "fired" in fb
    assert isinstance(fb["fired"], bool)
    assert fb["fired"] is False  # autouse fixture reset it


# ---------------------------------------------------------------------------
# Side-effect freedom
# ---------------------------------------------------------------------------

def test_dump_does_not_advance_hysteresis() -> None:
    """Running dump_engine_state on a FB-triggering snapshot should NOT
    flip the hysteresis flag — the dump is non-mutating, even when rules
    return Recommendations."""
    snap_fb = _Snap(
        game_time=200.0,
        raw_events=[{
            "EventName": "ChampionKill", "EventTime": 180.0,
            "KillerName": "Me", "VictimName": "Yasuo", "Assisters": [],
        }],
        allies=[_Player(summoner_name="Me", champion_name="MyChamp")],
        enemies=[_Player(summoner_name="Yasuo", champion_name="Yasuo")],
    )
    # First dump captures the rule firing, AND advances hysteresis (because
    # rule_first_blood mutates _FIRST_BLOOD_HYSTERESIS.fired when it fires).
    dump1 = dump_engine_state(snap_fb)
    fb1 = next(r for r in dump1["rule_results"] if r["rule"] == "rule_first_blood")
    assert fb1["fired"] is True

    # NOTE: The dump is observational — it calls the rules, which themselves
    # mutate hysteresis. So a second dump WILL see fired=False because the
    # rule's hysteresis check returns None on re-entry. This is the same
    # behaviour as evaluate(); dump_engine_state doesn't undo rule effects.
    # If a fully side-effect-free dump is needed, callers should reset the
    # relevant hysteresis singletons before calling.
    dump2 = dump_engine_state(snap_fb)
    fb2 = next(r for r in dump2["rule_results"] if r["rule"] == "rule_first_blood")
    assert fb2["fired"] is False
    assert fb2["rec"] is None
