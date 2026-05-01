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
# Color profiles — one profile per camp TYPE (both sides share the same
# icon color). Calibrated for SR minimap icons; deliberately conservative
# to bias false-NEGATIVE over false-positive.
# --------------------------------------------------------------------------
_RED_BUFF  = ColorProfile(hue_min=0,   hue_max=10,  sat_min=150, val_min=150)
_BLUE_BUFF = ColorProfile(hue_min=100, hue_max=130, sat_min=150, val_min=150)
_SCUTTLE   = ColorProfile(hue_min=40,  hue_max=60,  sat_min=140, val_min=140)
_GROMP     = ColorProfile(hue_min=55,  hue_max=80,  sat_min=120, val_min=120)
_KRUGS     = ColorProfile(hue_min=10,  hue_max=30,  sat_min=100, val_min=100)
_WOLVES    = ColorProfile(hue_min=100, hue_max=130, sat_min=80,  val_min=110)
_RAPTORS   = ColorProfile(hue_min=5,   hue_max=20,  sat_min=140, val_min=140)

CAMP_COLOR_PROFILES: Final[dict[str, ColorProfile]] = {
    "order_red_buff":  _RED_BUFF,
    "order_blue_buff": _BLUE_BUFF,
    "order_gromp":     _GROMP,
    "order_krugs":     _KRUGS,
    "order_raptors":   _RAPTORS,
    "order_wolves":    _WOLVES,
    "order_scuttle":   _SCUTTLE,
    "chaos_red_buff":  _RED_BUFF,
    "chaos_blue_buff": _BLUE_BUFF,
    "chaos_gromp":     _GROMP,
    "chaos_krugs":     _KRUGS,
    "chaos_raptors":   _RAPTORS,
    "chaos_wolves":    _WOLVES,
    "chaos_scuttle":   _SCUTTLE,
}


# --------------------------------------------------------------------------
# Default capture rects — 1080p, default minimap (bottom-right).
# Minimap assumed at approx (1720, 880) with 200×200 px extent.
# Derived from normalized CAMP_POSITIONS via:
#   screen_x = minimap_left + norm_x * minimap_w - 12
#   screen_y = minimap_top  + norm_y * minimap_h - 12
# Each region is 24×24 around the camp icon center. Users will need to
# recalibrate for non-1080p or non-default minimap sizes via settings.
# --------------------------------------------------------------------------
DEFAULT_CAPTURE_REGIONS: Final[dict[str, CaptureRegion]] = {
    # Order side (blue side) — bottom-left quadrant of minimap
    "order_blue_buff": CaptureRegion(left=1752, top=1018, width=24, height=24),
    "order_red_buff":  CaptureRegion(left=1784, top=1040, width=24, height=24),
    "order_gromp":     CaptureRegion(left=1736, top=994,  width=24, height=24),
    "order_wolves":    CaptureRegion(left=1758, top=994,  width=24, height=24),
    "order_raptors":   CaptureRegion(left=1788, top=1022, width=24, height=24),
    "order_krugs":     CaptureRegion(left=1768, top=1054, width=24, height=24),
    "order_scuttle":   CaptureRegion(left=1794, top=1008, width=24, height=24),
    # Chaos side (red side) — top-right quadrant of minimap
    "chaos_blue_buff": CaptureRegion(left=1864, top=958,  width=24, height=24),
    "chaos_red_buff":  CaptureRegion(left=1832, top=936,  width=24, height=24),
    "chaos_gromp":     CaptureRegion(left=1880, top=982,  width=24, height=24),
    "chaos_wolves":    CaptureRegion(left=1858, top=982,  width=24, height=24),
    "chaos_raptors":   CaptureRegion(left=1828, top=954,  width=24, height=24),
    "chaos_krugs":     CaptureRegion(left=1848, top=922,  width=24, height=24),
    "chaos_scuttle":   CaptureRegion(left=1822, top=968,  width=24, height=24),
}


# Worker loop cadence — 500 ms = 2 Hz, well under any combat-relevant
# threshold. Higher rates buy nothing for camps that respawn every
# 2:15+.
LOOP_INTERVAL_S: Final[float] = 0.5

# How many consecutive failures (capture or detect) before the service
# disables itself. One bad frame is normal; a sustained stream means
# something's wrong (locked screen, permission revoked, etc.).
MAX_CONSECUTIVE_FAILURES: Final[int] = 5
