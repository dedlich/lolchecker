"""Tests for power spike detection."""
from __future__ import annotations

from champ_assistant.lcda.power_spikes import (
    PowerSpike,
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
