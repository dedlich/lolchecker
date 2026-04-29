"""Heuristic detector — "is the in-game scoreboard currently shown?".

The League scoreboard is a dark, semi-translucent UI band that
overlays the top-center of the screen when the user holds TAB. When
visible, that screen region is:

  * dark (mean luminance < ~80 on a 0-255 scale)
  * uniform (low pixel-variance — it's a flat UI background)

Without the scoreboard, the same region shows live game pixels —
champions, particle effects, terrain — which are bright and
high-variance.

The detector returns a verdict + the variance value so the caller
can log "barely-detected" cases for diagnostics tuning.

Calibration caveat
==================
The thresholds and capture region are placeholders calibrated for
1080p with a default UI scale. Real users will need to verify the
region + thresholds against their setup; that's part of the same
calibration loop as Stage A camp detection. No automatic detection.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ScoreboardThresholds:
    """Tunable thresholds. Defaults target the dark/uniform UI band
    pattern of the standard League scoreboard at 1080p."""
    max_mean_brightness: float = 80.0   # average luminance must be below this
    max_variance: float = 800.0         # pixel-variance must be below this


def _to_grayscale(rgb: np.ndarray) -> np.ndarray:
    """Standard RGB → luminance via the BT.601 weights. Returns
    uint8 grayscale, same shape minus the channel axis."""
    if rgb.ndim < 2 or rgb.shape[-1] != 3:
        raise ValueError(f"expected RGB array, got shape {rgb.shape}")
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    return np.clip(gray, 0, 255).astype(np.uint8)


def detect_scoreboard_present(
    image_rgb: np.ndarray,
    thresholds: ScoreboardThresholds = ScoreboardThresholds(),
) -> tuple[bool, float, float]:
    """Return (present, mean_brightness, variance).

    Both numbers are surfaced for diagnostics + threshold tuning —
    a developer testing on their own setup can log mean+variance
    and adjust ``ScoreboardThresholds`` for their UI scale.
    """
    gray = _to_grayscale(image_rgb)
    mean_brightness = float(gray.mean())
    variance = float(gray.var())
    is_present = (
        mean_brightness < thresholds.max_mean_brightness
        and variance < thresholds.max_variance
    )
    return is_present, mean_brightness, variance


class ScoreboardPresenceDetector:
    """Class wrapper for parity with the spec's class-based API.
    Stateless — defers to the module-level pure function."""

    def __init__(self, thresholds: ScoreboardThresholds = ScoreboardThresholds()) -> None:
        self._thresholds = thresholds

    def detect(self, image_rgb: np.ndarray) -> bool:
        present, _, _ = detect_scoreboard_present(image_rgb, self._thresholds)
        return present

    def detect_with_metrics(self, image_rgb: np.ndarray) -> tuple[bool, float, float]:
        return detect_scoreboard_present(image_rgb, self._thresholds)
