"""Tests for HSV color-presence detection.

All tests use synthetic numpy arrays — no actual screen capture.
The real capture path is exercised by the service-lifecycle tests
via a stub MinimapCapture.
"""
from __future__ import annotations

import numpy as np
import pytest

from champ_assistant.vision.color_detector import (
    ColorPresenceDetector,
    detect_presence,
    rgb_to_hsv_opencv_range,
)
from champ_assistant.vision.config import ColorProfile


def _solid_rgb(r: int, g: int, b: int, *, size: int = 24) -> np.ndarray:
    """Build a (size, size, 3) uint8 RGB image filled with one color."""
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[..., 0] = r
    arr[..., 1] = g
    arr[..., 2] = b
    return arr


# ----------------------------------------------------------------------
# HSV conversion sanity
# ----------------------------------------------------------------------
def test_hsv_conversion_pure_red() -> None:
    """Pure red (255,0,0) → H=0, S=255, V=255 in OpenCV ranges."""
    rgb = _solid_rgb(255, 0, 0, size=2)
    hsv = rgb_to_hsv_opencv_range(rgb)
    # Allow ±1 tolerance for rounding.
    assert hsv[0, 0, 0] <= 1 or hsv[0, 0, 0] >= 179
    assert 250 <= hsv[0, 0, 1] <= 255
    assert 250 <= hsv[0, 0, 2] <= 255


def test_hsv_conversion_pure_blue() -> None:
    """Pure blue (0,0,255) → H≈120, S=255, V=255."""
    rgb = _solid_rgb(0, 0, 255, size=2)
    hsv = rgb_to_hsv_opencv_range(rgb)
    assert 118 <= hsv[0, 0, 0] <= 122
    assert 250 <= hsv[0, 0, 1] <= 255


def test_hsv_conversion_grey_has_zero_saturation() -> None:
    rgb = _solid_rgb(128, 128, 128, size=2)
    hsv = rgb_to_hsv_opencv_range(rgb)
    assert hsv[0, 0, 1] == 0
    assert hsv[0, 0, 2] == 128


def test_hsv_conversion_black_has_zero_value() -> None:
    rgb = _solid_rgb(0, 0, 0, size=2)
    hsv = rgb_to_hsv_opencv_range(rgb)
    assert hsv[0, 0, 2] == 0
    # Saturation undefined for black → should be 0 (no division by zero)
    assert hsv[0, 0, 1] == 0


# ----------------------------------------------------------------------
# detect_presence — positive cases
# ----------------------------------------------------------------------
def test_detect_red_buff_color_present() -> None:
    """A 24×24 patch of bright red should match the red_buff profile
    and produce ≥ pixel_threshold matching pixels."""
    img = _solid_rgb(220, 30, 30)  # saturated red
    profile = ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150)
    present, count = detect_presence(img, profile)
    assert present is True
    # 24×24 = 576 pixels — most should match.
    assert count > 500


def test_detect_blue_buff_color_present() -> None:
    img = _solid_rgb(30, 30, 220)
    profile = ColorProfile(hue_min=100, hue_max=130, sat_min=150, val_min=150)
    present, _ = detect_presence(img, profile)
    assert present is True


# ----------------------------------------------------------------------
# detect_presence — negative cases (false-positive prevention)
# ----------------------------------------------------------------------
def test_detect_grey_does_not_trigger_red_profile() -> None:
    """Low-saturation grey must not match a saturated-color profile.
    Critical for false-positive prevention."""
    img = _solid_rgb(128, 128, 128)
    profile = ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150)
    present, count = detect_presence(img, profile)
    assert present is False
    assert count == 0


def test_detect_dark_red_does_not_trigger() -> None:
    """Dark red (low value) must not match a high-value profile."""
    img = _solid_rgb(60, 10, 10)  # dim red
    profile = ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150)
    present, _ = detect_presence(img, profile)
    assert present is False


def test_detect_below_pixel_threshold_returns_false() -> None:
    """Mostly-grey image with a tiny red dot — must NOT trigger if the
    dot is smaller than pixel_threshold."""
    img = _solid_rgb(128, 128, 128, size=24)
    # Inject 3 red pixels — under the default threshold of 8.
    img[0, 0] = (220, 30, 30)
    img[0, 1] = (220, 30, 30)
    img[0, 2] = (220, 30, 30)
    profile = ColorProfile(
        hue_min=0, hue_max=10, sat_min=150, val_min=150,
        pixel_threshold=8,
    )
    present, count = detect_presence(img, profile)
    assert present is False
    assert count == 3


def test_detect_at_pixel_threshold_returns_true() -> None:
    """Exactly threshold many matching pixels triggers."""
    img = _solid_rgb(128, 128, 128, size=24)
    for i in range(8):
        img[0, i] = (220, 30, 30)
    profile = ColorProfile(
        hue_min=0, hue_max=10, sat_min=150, val_min=150,
        pixel_threshold=8,
    )
    present, count = detect_presence(img, profile)
    assert present is True
    assert count == 8


# ----------------------------------------------------------------------
# Determinism + class API
# ----------------------------------------------------------------------
def test_detect_is_deterministic() -> None:
    """Same input must produce same output every call (no randomness
    in the detection path)."""
    img = _solid_rgb(220, 30, 30)
    profile = ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150)
    results = {detect_presence(img, profile)[0] for _ in range(10)}
    assert results == {True}


def test_class_api_matches_function_api() -> None:
    detector = ColorPresenceDetector()
    img = _solid_rgb(220, 30, 30)
    profile = ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150)
    assert detector.detect_presence(img, profile) is True
