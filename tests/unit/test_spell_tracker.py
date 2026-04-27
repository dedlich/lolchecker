"""Tests for the summoner spell cooldown tracker."""
from __future__ import annotations

from champ_assistant.lcda.spell_tracker import SpellCooldown, SpellTracker


def test_mark_used_records_entry() -> None:
    tracker = SpellTracker()
    tracker.mark_used("Faker", "Flash", cooldown=300.0, game_time=600.0)
    entry = tracker.get("Faker", "Flash")
    assert entry is not None
    assert entry.cast_at == 600.0
    assert entry.cooldown == 300.0


def test_remaining_decreases_with_game_time() -> None:
    cd = SpellCooldown("Faker", "Flash", cast_at=600.0, cooldown=300.0)
    assert cd.remaining(600.0) == 300.0
    assert cd.remaining(750.0) == 150.0
    assert cd.remaining(900.0) == 0.0
    assert cd.remaining(1000.0) == 0.0  # floored


def test_zero_cooldown_means_unknown_spell() -> None:
    cd = SpellCooldown("X", "Mystery", cast_at=100.0, cooldown=0.0)
    assert cd.remaining(150.0) == 0.0
    assert cd.is_ready(150.0) is True


def test_reset_removes_entry() -> None:
    tracker = SpellTracker()
    tracker.mark_used("X", "Flash", 300.0, 600.0)
    tracker.reset("X", "Flash")
    assert tracker.get("X", "Flash") is None


def test_reset_summoner_clears_all_entries_for_player() -> None:
    tracker = SpellTracker()
    tracker.mark_used("X", "Flash", 300.0, 0.0)
    tracker.mark_used("X", "Ignite", 180.0, 0.0)
    tracker.mark_used("Y", "Flash", 300.0, 0.0)
    tracker.reset_summoner("X")
    assert tracker.get("X", "Flash") is None
    assert tracker.get("X", "Ignite") is None
    assert tracker.get("Y", "Flash") is not None


def test_gc_drops_ready_entries() -> None:
    tracker = SpellTracker()
    tracker.mark_used("A", "Flash", 300.0, 0.0)
    tracker.mark_used("B", "Ignite", 180.0, 100.0)
    removed = tracker.gc(game_time=400.0)  # both should be ready
    assert removed == 2
    assert len(tracker) == 0


def test_gc_keeps_active_entries() -> None:
    tracker = SpellTracker()
    tracker.mark_used("A", "Flash", 300.0, 100.0)  # ready at 400
    tracker.mark_used("B", "Smite", 90.0, 350.0)   # ready at 440
    removed = tracker.gc(game_time=420.0)
    assert removed == 1
    assert tracker.get("A", "Flash") is None
    assert tracker.get("B", "Smite") is not None


def test_remaining_via_tracker_helper() -> None:
    tracker = SpellTracker()
    tracker.mark_used("X", "Flash", 300.0, 600.0)
    assert tracker.remaining("X", "Flash", 750.0) == 150.0
    # No entry → 0.0 (treated as ready)
    assert tracker.remaining("Y", "Flash", 750.0) == 0.0
