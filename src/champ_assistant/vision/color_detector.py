"""HSV color-presence detector — pure numpy, no opencv.

The detector takes an RGB image (24×24×3 uint8 typical) + a
``ColorProfile`` and returns True iff at least ``pixel_threshold``
pixels fall inside the profile's HSV bounds.

Implementation notes
====================
* OpenCV-compatible HSV ranges (H 0-180, S/V 0-255). Not the
  more-common 0-360/0-100 because the inline color-tuning
  references in the spec / community use OpenCV ranges.
* HSV conversion is vectorized over the whole array — one
  numpy max/min/where pass per channel. Constant-time per pixel,
  no Python loops.
* Hue is circular: profiles spanning the 0/180 boundary (e.g.
  hue_min=170, hue_max=10 for full red) work via a "min > max"
  branch that ORs two hue bands.
* No allocation surprises — we cast to uint16 for the safe-subtract
  in the H computation, but it's all stack-aligned numpy ops.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .config import ColorProfile


def rgb_to_hsv_opencv_range(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB uint8 (..., 3) to HSV with OpenCV-style ranges
    (H 0-180, S 0-255, V 0-255), uint8 output.

    This matches what cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV) returns,
    so HSV thresholds copied from any opencv tutorial work directly.

    The color-cube math is straightforward:
      V = max(R, G, B)
      S = (V - min) / V * 255   (0 if V == 0)
      H = piecewise based on which channel is V

    All in-place via numpy so the only allocations are the output
    array and a couple of intermediate masks.
    """
    if rgb.dtype != np.uint8:
        rgb = rgb.astype(np.uint8)
    if rgb.ndim < 2 or rgb.shape[-1] != 3:
        raise ValueError(f"expected (..., 3) RGB array, got shape {rgb.shape}")

    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # V channel — direct copy of cmax.
    v = cmax.astype(np.uint8)

    # S channel — (delta / cmax) * 255, with safe-divide where cmax==0.
    s = np.zeros_like(cmax, dtype=np.uint8)
    nonzero = cmax > 0
    s_full = np.zeros_like(cmax, dtype=np.float32)
    s_full[nonzero] = delta[nonzero].astype(np.float32) * 255.0 / cmax[nonzero].astype(np.float32)
    s = np.clip(s_full, 0, 255).astype(np.uint8)

    # H channel — opencv-style piecewise. Output range 0..180 (NOT 0..360),
    # achieved by halving the standard H computation (which is in 0..360).
    h = np.zeros_like(cmax, dtype=np.float32)
    delta_safe = np.where(delta == 0, 1, delta)  # avoid div-by-zero; mask below

    r_is_max = (cmax == r) & (delta > 0)
    g_is_max = (cmax == g) & (delta > 0) & ~r_is_max
    b_is_max = (cmax == b) & (delta > 0) & ~r_is_max & ~g_is_max

    # Standard formula gives 0..360; we want 0..180, so each branch is
    # halved (the "/ 2" multiplications below).
    h[r_is_max] = (((g[r_is_max] - b[r_is_max]) / delta_safe[r_is_max]) % 6) * 30
    h[g_is_max] = (((b[g_is_max] - r[g_is_max]) / delta_safe[g_is_max]) + 2) * 30
    h[b_is_max] = (((r[b_is_max] - g[b_is_max]) / delta_safe[b_is_max]) + 4) * 30
    h_uint8 = np.clip(h, 0, 180).astype(np.uint8)

    out = np.empty_like(rgb)
    out[..., 0] = h_uint8
    out[..., 1] = s
    out[..., 2] = v
    return out


def detect_presence(image: np.ndarray, profile: "ColorProfile") -> tuple[bool, int]:
    """Return ``(present, matching_pixel_count)``.

    ``image`` is RGB uint8 (..., 3). The profile's HSV bounds are
    applied directly; profiles where ``hue_min > hue_max`` wrap
    around 180 (full-red case).

    Returns the matching pixel count along with the boolean so
    diagnostics can show "barely-detected" vs "strongly-detected"
    cases without re-running the check.
    """
    hsv = rgb_to_hsv_opencv_range(image)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    if profile.hue_min <= profile.hue_max:
        hue_match = (h >= profile.hue_min) & (h <= profile.hue_max)
    else:
        # Wrap-around: e.g. hue_min=170, hue_max=10 for full red.
        hue_match = (h >= profile.hue_min) | (h <= profile.hue_max)

    sat_match = s >= profile.sat_min
    val_match = v >= profile.val_min
    matching = int(np.count_nonzero(hue_match & sat_match & val_match))
    return matching >= profile.pixel_threshold, matching


class ColorPresenceDetector:
    """Thin object wrapper for parity with the spec's class-based API.

    State-free — the heavy lifting is in the module-level
    ``detect_presence``. Kept as a class so future extensions (per-
    detector calibration, cached masks) have a place to live.
    """

    def detect_presence(
        self, image: np.ndarray, profile: "ColorProfile",
    ) -> bool:
        present, _ = detect_presence(image, profile)
        return present
