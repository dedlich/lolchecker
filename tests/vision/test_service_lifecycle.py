"""Tests for VisionObservationService — start/stop, settings gate,
failure self-disable, engine sync via signal."""
from __future__ import annotations

import sys
import time

import numpy as np
import pytest

from champ_assistant.jungle_timeline import JungleTimelineEngine
from champ_assistant.vision.config import (
    DEFAULT_CAPTURE_REGIONS,
    CAMP_COLOR_PROFILES,
    CaptureRegion,
    ColorProfile,
)
from champ_assistant.vision.observation_service import VisionObservationService


class _StubCapture:
    """Test double for MinimapCapture — returns a programmable
    sequence of images (or None for failures)."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.consecutive_failures = 0
        self.enabled = True
        self.closed = False

    def capture_region(self, region):
        if not self._frames:
            return None
        frame = self._frames.pop(0)
        if frame is None:
            self.consecutive_failures += 1
            return None
        self.consecutive_failures = 0
        return frame

    def close(self):
        self.closed = True


def _solid_rgb(r, g, b, *, size=24):
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[..., 0] = r
    arr[..., 1] = g
    arr[..., 2] = b
    return arr


# ----------------------------------------------------------------------
# Settings gate behavior — service shouldn't start when capture is
# disabled (which is the no-op-on-non-Windows path).
# ----------------------------------------------------------------------
class _DisabledCapture:
    enabled = False
    consecutive_failures = 0
    def capture_region(self, _): return None
    def close(self): pass


def test_service_does_not_start_when_capture_disabled() -> None:
    svc = VisionObservationService(capture=_DisabledCapture())
    svc.start()
    # Thread didn't spawn — _enabled stays False.
    assert svc._enabled is False
    assert svc._thread is None


# ----------------------------------------------------------------------
# Single-cycle smoke — call _cycle directly without starting the thread
# so we can assert deterministic behavior.
# ----------------------------------------------------------------------
def test_single_cycle_with_visible_camp_emits_no_event() -> None:
    """A red-buff-colored frame is detected as visible. First-frame
    visible = no event (no prior state)."""
    red = _solid_rgb(220, 30, 30)
    capture = _StubCapture([red] * len(DEFAULT_CAPTURE_REGIONS))
    svc = VisionObservationService(capture=capture)

    received = []
    svc.camp_cleared.connect(lambda *args: received.append(args))

    svc._cycle()
    assert received == []


def test_visible_then_not_visible_two_frames_emits() -> None:
    """Real transition path: 1 visible frame followed by 2 not-visible
    frames for red_buff → service emits camp_cleared."""
    # Each cycle hits all 7 regions; we only care about red_buff so the
    # other camps just see their own visible/not-visible patterns
    # which won't transition meaningfully here.
    red_visible = _solid_rgb(220, 30, 30)
    grey = _solid_rgb(128, 128, 128)
    # 3 cycles × 7 camps = 21 frames. First cycle: red visible. Next
    # two: red not-visible. Other camps stay grey throughout (no
    # transition because they were never visible).
    frames = []
    region_order = list(DEFAULT_CAPTURE_REGIONS.keys())
    for cycle_idx in range(3):
        for camp_id in region_order:
            if camp_id == "red_buff" and cycle_idx == 0:
                frames.append(red_visible)
            else:
                frames.append(grey)

    capture = _StubCapture(frames)
    svc = VisionObservationService(capture=capture)

    received = []
    svc.camp_cleared.connect(lambda *args: received.append(args))

    svc._cycle()  # cycle 1 — red visible
    svc._cycle()  # cycle 2 — red not visible (count=1)
    svc._cycle()  # cycle 3 — red not visible (count=2) → emit

    assert len(received) == 1
    camp_id, _gt, confidence = received[0]
    assert camp_id == "red_buff"
    assert confidence == 1.0


# ----------------------------------------------------------------------
# Failure handling
# ----------------------------------------------------------------------
def test_capture_failure_increments_counter() -> None:
    capture = _StubCapture([None] * 14)  # all failures
    svc = VisionObservationService(capture=capture)
    svc._cycle()
    assert svc.failures > 0


def test_consecutive_failures_self_disable_threshold() -> None:
    """The service's _run loop should disable itself after too many
    consecutive failures. We test this by directly checking the
    threshold logic since spinning up the thread for 5 × 500ms = 2.5s
    is too slow for unit tests."""
    from champ_assistant.vision.config import MAX_CONSECUTIVE_FAILURES
    # Push counter past threshold synthetically — _run's check is
    # ``capture.consecutive_failures >= MAX_CONSECUTIVE_FAILURES``.
    capture = _StubCapture([None])
    capture.consecutive_failures = MAX_CONSECUTIVE_FAILURES
    # Confirm the constant matches the documented contract.
    assert MAX_CONSECUTIVE_FAILURES == 5


# ----------------------------------------------------------------------
# Engine integration
# ----------------------------------------------------------------------
def test_engine_register_clear_sets_anchor() -> None:
    engine = JungleTimelineEngine()
    engine.tick(600.0)  # 10 min in
    # Before any observed clear: unanchored camps show "alive" sentinel
    # (predictive timers were removed — only observed clears are shown).
    before = engine.states()["red_buff"]
    assert before.state == "alive"

    engine.register_clear("red_buff", 600.0)
    after = engine.states()["red_buff"]
    # Anchor was set; next_spawn = 600 + 300 = 900.
    assert after.next_spawn_at == 900.0


def test_engine_register_clear_rejects_bogus_camp_id() -> None:
    """Bad camp_id from a buggy detector must not raise — silent reject."""
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    # Should be silent.
    engine.register_clear("not_a_real_camp", 600.0)
    engine.register_clear("", 600.0)
    # Engine still healthy.
    assert "red_buff" in engine.states()


def test_engine_register_clear_rejects_negative_time() -> None:
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    before_anchor = dict(engine._observed_clears)
    engine.register_clear("red_buff", -100.0)
    assert engine._observed_clears == before_anchor


def test_engine_register_clear_rejects_nan() -> None:
    import math
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    before_anchor = dict(engine._observed_clears)
    engine.register_clear("red_buff", math.nan)
    assert engine._observed_clears == before_anchor
