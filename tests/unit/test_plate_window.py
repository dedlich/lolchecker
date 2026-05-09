"""Tests for rule_plate_window — turret-plate despawn reminder (B3)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from champ_assistant.advisor.decision_engine import (
    PLATE_WINDOW_CLOSE_S,
    PLATE_WINDOW_OPEN_S,
    Recommendation,
    _suppress_dominated,
    reset_plate_window_hysteresis,
    rule_plate_window,
)


@pytest.fixture(autouse=True)
def _reset_state():
    reset_plate_window_hysteresis()
    yield
    reset_plate_window_hysteresis()


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

@dataclass
class _Snap:
    game_time: float = 600.0
    enemies: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    raw_events: list = field(default_factory=list)
    active_team: str = "ORDER"
    active_summoner: str = "Me"
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    enemy_spikes: list = field(default_factory=list)
    gank_alert: object = None
    tilt_state: object = None
    active_combat: object = None
    lane_opponent_alert: object = None
    game_result: str = ""


# ---------------------------------------------------------------------------
# Window guards
# ---------------------------------------------------------------------------

def test_silent_before_window_opens() -> None:
    """At 12:00 — too early, plates aren't despawning yet."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S - 60))
    assert rec is None


def test_silent_at_window_close() -> None:
    """At 14:00 — plates have already despawned."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_CLOSE_S))
    assert rec is None


def test_silent_after_window_close() -> None:
    """After 14:00 — too late."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_CLOSE_S + 30))
    assert rec is None


# ---------------------------------------------------------------------------
# Tier firing
# ---------------------------------------------------------------------------

def test_fires_at_window_open() -> None:
    """At exactly 13:00 — fire info."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S))
    assert rec is not None
    assert rec.severity == "info"
    assert rec.kind == "plate_window"
    assert "Plate" in rec.text


def test_fires_mid_window() -> None:
    """At 13:30 — fire with the right countdown."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S + 30))
    assert rec is not None
    # 30s remaining when game_time = 13:30 (close at 14:00)
    assert "30s" in rec.text


def test_message_mentions_plate_value() -> None:
    """v1.10.131 trim: plate-value detail moved from headline (which
    overran the 60-char in-game cap at ~95 chars) into the reasons
    chain so the InsightPanel still surfaces it on expand. Headline
    is the directive only; reasons carry the WHY."""
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S + 10))
    assert rec is not None
    assert "160g" in " ".join(rec.reasons)


# ---------------------------------------------------------------------------
# Hysteresis — fire once per game
# ---------------------------------------------------------------------------

def test_does_not_re_fire_after_first() -> None:
    snap1 = _Snap(game_time=PLATE_WINDOW_OPEN_S + 5)
    snap2 = _Snap(game_time=PLATE_WINDOW_OPEN_S + 25)
    first = rule_plate_window(snap1)
    second = rule_plate_window(snap2)
    assert first is not None
    assert second is None


def test_reset_re_arms_after_new_game() -> None:
    rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S + 10))
    reset_plate_window_hysteresis()
    rec = rule_plate_window(_Snap(game_time=PLATE_WINDOW_OPEN_S + 20))
    assert rec is not None


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------

def _rec(kind: str, severity: str = "warn") -> Recommendation:
    return Recommendation(
        text="x", severity=severity, category="objective",
        confidence=0.7, risk="LOW", ttl_s=10.0, kind=kind,
    )


def test_suppressed_by_ace() -> None:
    recs = [_rec("ace", "alert"), _rec("plate_window", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "plate_window" for r in out)


def test_suppressed_by_numbers_disadv() -> None:
    """Don't tell short-handed players to push for plates."""
    recs = [_rec("numbers_disadv", "warn"), _rec("plate_window", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "plate_window" for r in out)


def test_suppressed_by_ally_inhib_down() -> None:
    """Plates moot when defending your own base."""
    recs = [_rec("ally_inhib_down", "alert"), _rec("plate_window", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "plate_window" for r in out)


def test_suppressed_by_spiral_tilt() -> None:
    recs = [_rec("tilt", "alert"), _rec("plate_window", "info")]
    out = _suppress_dominated(recs)
    assert not any(r.kind == "plate_window" for r in out)


def test_survives_normal_tilt() -> None:
    """Non-spiral tilt + plate window: plates info still actionable."""
    recs = [_rec("tilt", "warn"), _rec("plate_window", "info")]
    out = _suppress_dominated(recs)
    assert any(r.kind == "plate_window" for r in out)
