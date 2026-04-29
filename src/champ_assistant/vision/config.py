"""Tunable configuration for the vision subsystem.

Two pieces of state per camp:

  * a ``ColorProfile`` — HSV-range heuristic for "is this camp icon
    visible in this 24×24 region right now?". Tunable values are
    documented inline.
  * a ``CaptureRegion`` — screen-pixel rectangle to grab. There is
    NO automatic minimap-detection at this stage; the user has to
    calibrate these to their resolution/minimap-scale combo. Defaults
    target 1080p with a default minimap on the right side of the
    screen.

Color values use OpenCV-style HSV ranges (H 0-180, S 0-255, V 0-255)
since that's the convention every other camp-detection write-up uses.
The presence check works on numpy arrays directly — no opencv import.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class ColorProfile:
    """HSV bounds + pixel-count threshold for a camp icon."""
    hue_min: int
    hue_max: int
    sat_min: int
    val_min: int
    pixel_threshold: int = 8


@dataclass(frozen=True)
class CaptureRegion:
    """Absolute screen-pixel rect. ``mss.grab({left, top, width, height})``
    expects exactly these fields."""
    left: int
    top: int
    width: int
    height: int


# --------------------------------------------------------------------------
# Color profiles — calibrated for the canonical SR minimap icons. Values
# are deliberately conservative (high sat/val mins, narrow hue band) to
# bias toward false-NEGATIVE rather than false-positive — a missed
# clear leaves the deterministic engine cycle as-is, a false detection
# would corrupt the timer.
# --------------------------------------------------------------------------
CAMP_COLOR_PROFILES: Final[dict[str, ColorProfile]] = {
    # Red Buff: bright orange-red icon
    "red_buff": ColorProfile(hue_min=0, hue_max=10, sat_min=150, val_min=150),
    # Blue Buff: saturated blue icon
    "blue_buff": ColorProfile(hue_min=100, hue_max=130, sat_min=150, val_min=150),
    # Scuttle: yellow/gold
    "scuttle": ColorProfile(hue_min=40, hue_max=60, sat_min=140, val_min=140),
    # Gromp: green-ish
    "gromp": ColorProfile(hue_min=55, hue_max=80, sat_min=120, val_min=120),
    # Krugs: brown/tan — low-saturation profile, less reliable
    "krugs": ColorProfile(hue_min=10, hue_max=30, sat_min=100, val_min=100),
    # Wolves: blue-grey
    "wolves": ColorProfile(hue_min=100, hue_max=130, sat_min=80, val_min=110),
    # Raptors: orange-red, distinguishable from Red Buff via position
    "raptors": ColorProfile(hue_min=5, hue_max=20, sat_min=140, val_min=140),
}


# --------------------------------------------------------------------------
# Default capture rects — placeholder values targeting 1080p with a
# default minimap. Real users WILL need to override these via settings
# once we expose them; for the first experimental iteration these are
# baseline defaults the developer can tune locally.
# Coords assume the minimap is in the bottom-right (League default for
# Blue side player). Each region is 24×24 around the camp icon.
# --------------------------------------------------------------------------
DEFAULT_CAPTURE_REGIONS: Final[dict[str, CaptureRegion]] = {
    "red_buff":  CaptureRegion(left=1670, top=982, width=24, height=24),
    "blue_buff": CaptureRegion(left=1832, top=820, width=24, height=24),
    "gromp":     CaptureRegion(left=1815, top=850, width=24, height=24),
    "krugs":     CaptureRegion(left=1700, top=995, width=24, height=24),
    "raptors":   CaptureRegion(left=1730, top=920, width=24, height=24),
    "wolves":    CaptureRegion(left=1790, top=865, width=24, height=24),
    "scuttle":   CaptureRegion(left=1770, top=900, width=24, height=24),
}


# Worker loop cadence — 500 ms = 2 Hz, well under any combat-relevant
# threshold. Higher rates buy nothing for camps that respawn every
# 2:15+.
LOOP_INTERVAL_S: Final[float] = 0.5

# How many consecutive failures (capture or detect) before the service
# disables itself. One bad frame is normal; a sustained stream means
# something's wrong (locked screen, permission revoked, etc.).
MAX_CONSECUTIVE_FAILURES: Final[int] = 5
