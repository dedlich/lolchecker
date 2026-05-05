"""Tests for power spike detection."""
from __future__ import annotations

from champ_assistant.lcda.power_spikes import (
    EnemySpike,
    PowerSpike,
    count_legendaries,
    detect_enemy_spikes,
    detect_spikes,
    extract_active_state,
)


def test_no_spikes_when_nothing_changed() -> None:
    assert detect_spikes(prev_level=5, new_level=5,
                        prev_items=1, new_items=1) == []


def test_level_six_spike_detected() -> None:
    spikes = detect_spikes(prev_level=5, new_level=6,
                          prev_items=0, new_items=0)
    assert len(spikes) == 1
    assert spikes[0].kind == "level"
    assert spikes[0].value == 6
    assert "Ultimate" in spikes[0].label


def test_double_level_spike_when_jumping_two_thresholds() -> None:
    """Crossing two milestones in one tick fires both."""
    spikes = detect_spikes(prev_level=5, new_level=11,
                          prev_items=0, new_items=0)
    assert {s.value for s in spikes if s.kind == "level"} == {6, 11}


def test_item_spike_one_then_two() -> None:
    spikes = detect_spikes(prev_level=10, new_level=10,
                          prev_items=0, new_items=2)
    values = sorted(s.value for s in spikes if s.kind == "items")
    assert values == [1, 2]


def test_level_and_item_spike_combined() -> None:
    spikes = detect_spikes(prev_level=5, new_level=6,
                          prev_items=0, new_items=1)
    kinds = {s.kind for s in spikes}
    assert kinds == {"level", "items"}


def test_no_regression_on_lower_values() -> None:
    """Item count can only go up; if it appears to drop (e.g. selling),
    we shouldn't fire spikes."""
    spikes = detect_spikes(prev_level=10, new_level=10,
                          prev_items=3, new_items=2)
    assert spikes == []


def test_extract_active_state_counts_legendary_items() -> None:
    active = {
        "level": 9,
        "items": [
            {"price": 1100},   # boots — skipped
            {"price": 3000},   # legendary
            {"price": 850},    # component — skipped
            {"price": 2400},   # legendary
        ],
    }
    level, items = extract_active_state(active)
    assert level == 9
    assert items == 2


def test_extract_active_state_empty() -> None:
    assert extract_active_state(None) == (0, 0)
    assert extract_active_state({}) == (0, 0)


def test_power_spike_dataclass_is_frozen() -> None:
    s = PowerSpike("level", 6, "L", "D")
    try:
        s.value = 11  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        return
    raise AssertionError("PowerSpike should be frozen")


# ---------------------------------------------------------------------------
# count_legendaries
# ---------------------------------------------------------------------------

def test_count_legendaries_filters_boots_and_components() -> None:
    items = [
        {"price": 1100},   # boots — skipped
        {"price": 3000},   # legendary ✓
        {"price": 850},    # component — skipped
        {"price": 2400},   # legendary ✓
    ]
    assert count_legendaries(items) == 2


def test_count_legendaries_empty() -> None:
    assert count_legendaries([]) == 0


# ---------------------------------------------------------------------------
# detect_enemy_spikes
# ---------------------------------------------------------------------------

def _player(name: str, prices: list[int]) -> dict:
    return {
        "championName": name,
        "items": [{"price": p} for p in prices],
        "team": "CHAOS",
    }


def test_detect_enemy_spikes_fires_on_first_legendary() -> None:
    players = [_player("Jinx", [2800])]
    spikes, new_counts = detect_enemy_spikes({}, players)
    assert len(spikes) == 1
    assert spikes[0].champion_name == "Jinx"
    assert spikes[0].legendary_count == 1


def test_detect_enemy_spikes_fires_on_second_legendary() -> None:
    prev = {"Jinx": 1}
    players = [_player("Jinx", [2800, 3200])]
    spikes, _ = detect_enemy_spikes(prev, players)
    assert len(spikes) == 1
    assert spikes[0].legendary_count == 2


def test_detect_enemy_spikes_no_spike_when_unchanged() -> None:
    prev = {"Jinx": 2}
    players = [_player("Jinx", [2800, 3200])]
    spikes, _ = detect_enemy_spikes(prev, players)
    assert spikes == []


def test_detect_enemy_spikes_multiple_champions() -> None:
    prev = {}
    players = [
        _player("Jinx", [2800]),
        _player("Thresh", [900]),   # cheap, not legendary
    ]
    spikes, _ = detect_enemy_spikes(prev, players)
    names = [s.champion_name for s in spikes]
    assert "Jinx" in names
    assert "Thresh" not in names


def test_detect_enemy_spikes_updates_counts() -> None:
    players = [_player("Jinx", [2800, 3000])]
    _, new_counts = detect_enemy_spikes({}, players)
    assert new_counts.get("Jinx") == 2


def test_detect_enemy_spikes_skips_missing_champion_name() -> None:
    players = [{"championName": "", "items": [{"price": 3000}], "team": "CHAOS"}]
    spikes, _ = detect_enemy_spikes({}, players)
    assert spikes == []


# ---------------------------------------------------------------------------
# rule_enemy_item_spike (decision engine)
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field


@dataclass
class _ESSnap:
    game_time: float = 600.0
    enemy_spikes: list = field(default_factory=list)
    allies: list = field(default_factory=list)
    enemies: list = field(default_factory=list)
    ally_aggregate: object = None
    enemy_aggregate: object = None
    objectives: list = field(default_factory=list)
    raw_events: list = field(default_factory=list)
    active_team: str = ""
    active_summoner: str = ""
    active_level: int = 8
    active_items: int = 1
    new_spikes: list = field(default_factory=list)
    game_result: str = ""


from champ_assistant.advisor.decision_engine import rule_enemy_item_spike


def test_enemy_spike_rule_fires_on_second_legendary() -> None:
    snap = _ESSnap(enemy_spikes=[EnemySpike("Jinx", 2)])
    rec = rule_enemy_item_spike(snap)
    assert rec is not None
    assert rec.kind == "enemy_spike"
    assert rec.severity == "warn"
    assert "Jinx" in rec.text
    assert "2" in rec.text


def test_enemy_spike_rule_fires_info_on_first_legendary() -> None:
    snap = _ESSnap(enemy_spikes=[EnemySpike("Thresh", 1)])
    rec = rule_enemy_item_spike(snap)
    assert rec is not None
    assert rec.severity == "info"


def test_enemy_spike_rule_silent_when_no_spikes() -> None:
    snap = _ESSnap(enemy_spikes=[])
    assert rule_enemy_item_spike(snap) is None


def test_enemy_spike_rule_picks_highest_count() -> None:
    snap = _ESSnap(enemy_spikes=[EnemySpike("A", 1), EnemySpike("B", 3)])
    rec = rule_enemy_item_spike(snap)
    assert rec is not None
    assert "B" in rec.text  # B has 3 legendaries — more dangerous
