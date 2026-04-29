"""Minimap region capture — Windows-only, mss-backed.

Stage A is intentionally Windows-only. Capture on macOS/Wayland
needs platform-specific permission flow that's a separate task.
On non-Windows the constructor logs once and ``capture_region``
returns None.

Failure modes that disable the capture:
  * ``mss`` import fails (dep missing → user didn't install
    [vision] extra; constructor returns a disabled instance)
  * ``mss.grab`` raises (permission revoked, headless session,
    GPU driver issue)

The service layer counts consecutive failures and disables the
whole detection pipeline after the threshold; this class just
reports them.
"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

    from .config import CaptureRegion

logger = logging.getLogger(__name__)


class MinimapCapture:
    """Thin wrapper around ``mss``. Construct once per worker thread
    (mss instances are not thread-safe; each thread that captures
    must own its instance)."""

    def __init__(self) -> None:
        self._enabled = sys.platform == "win32"
        self._mss = None
        self._numpy = None
        self._failure_count = 0

        if not self._enabled:
            logger.info("[VISION] disabled: unsupported platform")
            return

        try:
            import mss  # type: ignore[import-untyped]
            import numpy as np
        except ImportError as exc:
            logger.info("[VISION] disabled: dep missing (%s)", exc)
            self._enabled = False
            return

        try:
            self._mss = mss.mss()
            self._numpy = np
        except Exception as exc:  # noqa: BLE001 — opening the screen is fragile
            logger.info("[VISION] disabled: mss init failed (%s)", exc)
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def consecutive_failures(self) -> int:
        return self._failure_count

    def capture_region(self, region: "CaptureRegion") -> "np.ndarray | None":
        """Grab a single rect as an RGB uint8 ndarray (H, W, 3).

        Returns ``None`` on any error. The caller is responsible for
        counting failures — we just report the absence.
        """
        if not self._enabled or self._mss is None or self._numpy is None:
            return None

        try:
            shot = self._mss.grab({
                "left": region.left,
                "top": region.top,
                "width": region.width,
                "height": region.height,
            })
            # mss returns BGRA; convert to RGB for the detector.
            arr = self._numpy.asarray(shot, dtype=self._numpy.uint8)
            # mss shape: (H, W, 4) BGRA — drop alpha + reverse to RGB.
            if arr.ndim == 3 and arr.shape[-1] == 4:
                rgb = arr[..., [2, 1, 0]]   # B,G,R,A → R,G,B
            else:
                rgb = arr
            self._failure_count = 0
            return rgb
        except Exception as exc:  # noqa: BLE001
            self._failure_count += 1
            if self._failure_count == 1:
                # Log only the first failure — don't spam.
                logger.info("[VISION] capture failed: %s", exc)
            return None

    def close(self) -> None:
        """Release the mss handle. Safe to call even when disabled."""
        if self._mss is not None:
            try:
                self._mss.close()
            except Exception:  # noqa: BLE001
                pass
            self._mss = None
