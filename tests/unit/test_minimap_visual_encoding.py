"""Tests for the UI-only confidence visual encoding on the minimap widget.

These cover the spec's three bands (high/mid/low → full/muted/approximate)
and the ≈ prefix logic. They are deliberately kept Qt-free by exercising
the pure formatter and band-mapping helpers — the actual stylesheet
output is exercised by the smoke-test demo script.
"""
from __future__ import annotations

from champ_assistant.jungle_timeline import CampState
from champ_assistant.ui.minimap_timers_widget import (
    APPROXIMATE_PREFIX,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    OPACITY_HIGH,
    OPACITY_LOW,
    OPACITY_MID,
    _band_for,
    _format_timer_text,
    _opacity_for,
)


def _state(confidence: float, *, time_remaining: float = 60.0) -> CampState:
    return CampState(
        id="x", name="x", state="respawning",
        next_spawn_at=time_remaining, time_remaining=time_remaining,
        confidence=confidence,
    )


# ----------------------------------------------------------------------
# Band classification
# ----------------------------------------------------------------------
def test_high_band_at_or_above_threshold() -> None:
    assert _band_for(1.0) == "high"
    assert _band_for(CONFIDENCE_HIGH) == "high"
    assert _band_for(CONFIDENCE_HIGH - 0.001) == "mid"


def test_mid_band_between_thresholds() -> None:
    assert _band_for(0.6) == "mid"
    assert _band_for(CONFIDENCE_LOW) == "mid"


def test_low_band_below_threshold() -> None:
    assert _band_for(CONFIDENCE_LOW - 0.001) == "low"
    assert _band_for(0.0) == "low"


def test_opacity_per_band() -> None:
    assert _opacity_for(1.0) == OPACITY_HIGH
    assert _opacity_for(0.6) == OPACITY_MID
    assert _opacity_for(0.2) == OPACITY_LOW


# ----------------------------------------------------------------------
# Approximate-mode text formatting
# ----------------------------------------------------------------------
def test_high_confidence_renders_plain_timer() -> None:
    text = _format_timer_text(_state(0.95, time_remaining=185.0))
    assert text == "3:05"
    assert APPROXIMATE_PREFIX not in text


def test_mid_confidence_does_not_show_approximate_prefix() -> None:
    """Per spec: the ≈ prefix is reserved for the LOW band only.
    Mid-band gets opacity reduction without text changes."""
    text = _format_timer_text(_state(0.5, time_remaining=60.0))
    assert text == "1:00"
    assert APPROXIMATE_PREFIX not in text


def test_low_confidence_prepends_approximate_prefix() -> None:
    text = _format_timer_text(_state(0.3, time_remaining=185.0))
    assert text == f"{APPROXIMATE_PREFIX}3:05"


def test_low_confidence_under_one_minute_keeps_zero_prefix() -> None:
    """Sub-minute readings should still render as 0:SS so the column
    width stays constant. ≈ prefix sits on top of that."""
    text = _format_timer_text(_state(0.2, time_remaining=42.0))
    assert text == f"{APPROXIMATE_PREFIX}0:42"


def test_zero_seconds_renders_as_zero_zero() -> None:
    """Edge case at the cycle boundary — UI must not show empty string."""
    text = _format_timer_text(_state(1.0, time_remaining=0.0))
    assert text == "0:00"


def test_threshold_boundaries_dont_double_classify() -> None:
    """Defensive: confidence exactly at the threshold must land in
    exactly one band, never both."""
    for conf in (0.0, CONFIDENCE_LOW, 0.5, CONFIDENCE_HIGH, 1.0):
        band = _band_for(conf)
        assert band in {"high", "mid", "low"}
