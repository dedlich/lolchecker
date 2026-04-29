"""Tests for camp visibility transition detection (N-frame confirmation)."""
from __future__ import annotations

from champ_assistant.vision.transition_detector import (
    CONFIRM_FRAMES,
    CampTransitionDetector,
)


def test_initial_visible_does_not_emit() -> None:
    """First frame seen as visible must not emit anything — there's
    no prior state to transition from."""
    det = CampTransitionDetector()
    assert det.process("red_buff", visible_now=True, now=0.0) is None


def test_initial_not_visible_does_not_emit() -> None:
    """If the camp was never visible, going to not-visible isn't a
    'clear' — it's just the initial state."""
    det = CampTransitionDetector()
    assert det.process("red_buff", visible_now=False, now=0.0) is None
    assert det.process("red_buff", visible_now=False, now=0.5) is None


def test_visible_then_one_not_visible_does_not_emit() -> None:
    """Single not-visible frame after visible — must wait for confirmation."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    assert det.process("red_buff", visible_now=False, now=0.5) is None


def test_visible_then_confirmed_not_visible_emits() -> None:
    """Two consecutive not-visible frames after visible → emit clear."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    det.process("red_buff", visible_now=False, now=0.5)
    event = det.process("red_buff", visible_now=False, now=1.0)
    assert event is not None
    assert event.camp_id == "red_buff"
    assert event.timestamp == 1.0
    assert event.confidence == 1.0


def test_visible_flicker_does_not_emit() -> None:
    """visible → not_visible → visible → not_visible (single frames each)
    must not produce a spurious event — that's exactly the noise the
    2-frame requirement filters out."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    det.process("red_buff", visible_now=False, now=0.5)
    det.process("red_buff", visible_now=True, now=1.0)
    event = det.process("red_buff", visible_now=False, now=1.5)
    # Only one not-visible frame after the latest visible — no emit.
    assert event is None


def test_only_one_event_per_clear() -> None:
    """Once a clear has been emitted, subsequent not-visible frames
    must NOT re-emit (dedup window)."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    det.process("red_buff", visible_now=False, now=0.5)
    det.process("red_buff", visible_now=False, now=1.0)  # emit here
    # Subsequent same-state frames must stay silent.
    assert det.process("red_buff", visible_now=False, now=1.5) is None
    assert det.process("red_buff", visible_now=False, now=5.0) is None
    assert det.process("red_buff", visible_now=False, now=10.0) is None


def test_dedup_window_blocks_quick_re_emit() -> None:
    """Even with a full visible→not_visible→visible→not_visible cycle,
    if it happens inside DEDUP_WINDOW_S we don't re-emit (real respawns
    are 2:15+, anything faster is double-firing)."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True,  now=0.0)
    det.process("red_buff", visible_now=False, now=0.5)
    det.process("red_buff", visible_now=False, now=1.0)  # emit
    # New visible → fresh state
    det.process("red_buff", visible_now=True,  now=2.0)
    det.process("red_buff", visible_now=False, now=2.5)
    event = det.process("red_buff", visible_now=False, now=3.0)
    # Within 30s — must not emit again.
    assert event is None


def test_each_camp_tracked_independently() -> None:
    """Two camps' state machines don't interact — clearing red_buff
    must not affect blue_buff's tracking."""
    det = CampTransitionDetector()
    det.process("red_buff",  visible_now=True,  now=0.0)
    det.process("blue_buff", visible_now=True,  now=0.0)
    det.process("red_buff",  visible_now=False, now=0.5)
    det.process("blue_buff", visible_now=True,  now=0.5)
    red_event  = det.process("red_buff",  visible_now=False, now=1.0)
    blue_event = det.process("blue_buff", visible_now=True,  now=1.0)
    assert red_event is not None
    assert blue_event is None  # blue still visible, no transition


def test_reset_clears_specific_camp() -> None:
    """reset(camp_id) drops state for one camp, others untouched."""
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    det.process("blue_buff", visible_now=True, now=0.0)
    det.reset("red_buff")
    # Red's tracker is fresh — first not-visible doesn't transition
    # (no previous_visible recorded).
    assert det.process("red_buff", visible_now=False, now=1.0) is None
    # Blue is unaffected.
    det.process("blue_buff", visible_now=False, now=1.0)
    blue_event = det.process("blue_buff", visible_now=False, now=1.5)
    assert blue_event is not None


def test_reset_all_clears_everything() -> None:
    det = CampTransitionDetector()
    det.process("red_buff", visible_now=True, now=0.0)
    det.process("blue_buff", visible_now=True, now=0.0)
    det.reset()
    # Both fresh.
    assert det.process("red_buff", visible_now=False, now=1.0) is None
    assert det.process("blue_buff", visible_now=False, now=1.0) is None


def test_confirm_frames_constant_is_2() -> None:
    """The 2-frame requirement is part of the documented contract."""
    assert CONFIRM_FRAMES == 2
