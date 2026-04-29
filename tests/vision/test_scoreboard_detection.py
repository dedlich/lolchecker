"""Tests for scoreboard visibility detector + 2-frame state machine."""
from __future__ import annotations

import numpy as np
import pytest

from champ_assistant.vision.scoreboard_detector import (
    ScoreboardPresenceDetector,
    ScoreboardThresholds,
    detect_scoreboard_present,
)
from champ_assistant.vision.scoreboard_visibility_service import (
    CONFIRM_FRAMES,
    ScoreboardVisibilityService,
)


def _dark_uniform(size_h: int = 40, size_w: int = 200, brightness: int = 30) -> np.ndarray:
    """Synthesize a frame that looks like the scoreboard background:
    dark + uniform across the whole region."""
    arr = np.full((size_h, size_w, 3), brightness, dtype=np.uint8)
    return arr


def _bright_noisy(size_h: int = 40, size_w: int = 200) -> np.ndarray:
    """Synthesize a frame that looks like live game pixels: bright +
    high-variance (a teamfight, terrain edges, particle effects)."""
    rng = np.random.default_rng(seed=42)
    arr = rng.integers(120, 255, size=(size_h, size_w, 3), dtype=np.uint8)
    return arr


# ----------------------------------------------------------------------
# Detector — pure heuristic
# ----------------------------------------------------------------------
def test_dark_uniform_image_detects_as_scoreboard() -> None:
    """Mean brightness ~30, variance ~0 — well below both thresholds."""
    img = _dark_uniform()
    present, mean_b, var = detect_scoreboard_present(img)
    assert present is True
    assert mean_b < 80
    assert var < 800


def test_bright_noisy_image_does_not_detect() -> None:
    """Mean brightness ~190, variance high — well above thresholds."""
    img = _bright_noisy()
    present, _, _ = detect_scoreboard_present(img)
    assert present is False


def test_dark_but_noisy_does_not_detect() -> None:
    """Dark mean but high contrast (e.g. dark game scene with bright
    particle effects / champion outlines) — variance must exceed the
    default threshold to filter this case out."""
    rng = np.random.default_rng(seed=42)
    # Mostly dark pixels with a few bright outliers — simulates
    # particle effects on a dark map. Mean stays low, variance high.
    arr = rng.integers(0, 30, size=(40, 200, 3), dtype=np.uint8)
    # Inject ~15% bright pixels to push variance well over 800.
    bright_mask = rng.random((40, 200)) < 0.15
    arr[bright_mask] = 255
    present, _, var = detect_scoreboard_present(arr)
    assert present is False
    assert var > 800  # confirms the high-contrast premise


def test_bright_uniform_does_not_detect() -> None:
    """A bright but flat region (sky, light terrain) shouldn't be
    confused with the dark scoreboard band."""
    img = np.full((40, 200, 3), 200, dtype=np.uint8)
    present, _, _ = detect_scoreboard_present(img)
    assert present is False


def test_threshold_tuning_works() -> None:
    """Detector exposes thresholds so it can be retuned for non-
    standard UI scales."""
    img = _dark_uniform(brightness=70)  # borderline
    strict = ScoreboardThresholds(max_mean_brightness=50.0)
    permissive = ScoreboardThresholds(max_mean_brightness=100.0)
    assert detect_scoreboard_present(img, strict)[0] is False
    assert detect_scoreboard_present(img, permissive)[0] is True


def test_class_api_with_metrics() -> None:
    detector = ScoreboardPresenceDetector()
    img = _dark_uniform()
    present, mean_b, var = detector.detect_with_metrics(img)
    assert present is True
    assert mean_b < 80
    assert var < 800


# ----------------------------------------------------------------------
# Visibility service — 2-frame state machine
# ----------------------------------------------------------------------
def test_state_machine_requires_2_frames_for_visibility() -> None:
    """Single visible frame must NOT flip confirmed_visible.
    Two consecutive frames must."""
    svc = ScoreboardVisibilityService()
    received: list[bool] = []
    svc.visibility_changed.connect(received.append)

    svc._process_frame_verdict(True)
    assert received == []
    svc._process_frame_verdict(True)
    assert received == [True]


def test_state_machine_requires_2_frames_for_hide() -> None:
    """After confirmed visible, a single not-visible frame must NOT
    flip back. Two consecutive must."""
    svc = ScoreboardVisibilityService()
    received: list[bool] = []
    svc.visibility_changed.connect(received.append)

    # Bring up.
    svc._process_frame_verdict(True)
    svc._process_frame_verdict(True)
    received.clear()

    # Single hide doesn't fire.
    svc._process_frame_verdict(False)
    assert received == []
    # Second confirms.
    svc._process_frame_verdict(False)
    assert received == [False]


def test_flicker_does_not_emit() -> None:
    """visible → not_visible → visible (single frames each) must not
    produce any transition."""
    svc = ScoreboardVisibilityService()
    received: list[bool] = []
    svc.visibility_changed.connect(received.append)

    # Confirm visible
    svc._process_frame_verdict(True)
    svc._process_frame_verdict(True)
    received.clear()

    svc._process_frame_verdict(False)  # candidate hide, count=1
    svc._process_frame_verdict(True)   # back to confirmed state, reset
    svc._process_frame_verdict(False)  # candidate hide again, count=1
    assert received == []


def test_no_emit_on_first_not_visible_when_confirmed_state_is_default() -> None:
    """Default confirmed_visible is False. A first not-visible frame
    matches confirmed state — must not emit."""
    svc = ScoreboardVisibilityService()
    received: list[bool] = []
    svc.visibility_changed.connect(received.append)

    svc._process_frame_verdict(False)
    svc._process_frame_verdict(False)
    assert received == []


def test_transitions_emitted_counter() -> None:
    svc = ScoreboardVisibilityService()
    assert svc.transitions_emitted == 0
    svc._process_frame_verdict(True)
    svc._process_frame_verdict(True)
    assert svc.transitions_emitted == 1
    svc._process_frame_verdict(False)
    svc._process_frame_verdict(False)
    assert svc.transitions_emitted == 2


def test_confirm_frames_constant() -> None:
    """The 2-frame requirement is part of the documented contract."""
    assert CONFIRM_FRAMES == 2
