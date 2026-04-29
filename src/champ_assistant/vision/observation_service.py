"""VisionObservationService — orchestrator for the camp-detection
worker thread.

Architecture
============
* Worker thread runs a 500ms loop: capture each camp region, run
  HSV color check, feed verdict into the transition detector.
* Detector emits CampClearedEvent only on confirmed visible →
  not_visible transitions (2-frame confirmation, 30s dedup).
* Service emits a Qt signal ``camp_cleared`` connected to the
  engine's ``register_clear`` via QueuedConnection — engine sees the
  call on the Qt main thread, no cross-thread state mutation.
* Diagnostics counters exposed as plain attributes; diagnostics
  reads them on its own 10s timer.
* Failure budget: MAX_CONSECUTIVE_FAILURES (config.py) before the
  whole service self-disables. UI never sees the failure.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from .. import telemetry
from .color_detector import detect_presence
from .config import (
    CAMP_COLOR_PROFILES,
    DEFAULT_CAPTURE_REGIONS,
    LOOP_INTERVAL_S,
    MAX_CONSECUTIVE_FAILURES,
    CaptureRegion,
    ColorProfile,
)
from .transition_detector import CampClearedEvent, CampTransitionDetector

if TYPE_CHECKING:
    from .capture import MinimapCapture

logger = logging.getLogger(__name__)


class VisionObservationService(QObject):
    """Background camp-detection thread + Qt signal bridge.

    Constructor doesn't start the thread — caller decides via
    ``start()`` whether the settings flag enables the feature.
    """

    # Emitted on every confirmed clear. Connected to engine.register_clear
    # via Qt.QueuedConnection so the engine call happens on the main thread.
    camp_cleared = pyqtSignal(str, float, float)  # camp_id, game_time_anchor, confidence

    def __init__(
        self,
        *,
        capture: "MinimapCapture | None" = None,
        regions: dict[str, CaptureRegion] | None = None,
        profiles: dict[str, ColorProfile] | None = None,
        game_time_provider=None,  # callable() -> float | None
    ) -> None:
        super().__init__()
        self._capture = capture
        self._regions = regions if regions is not None else DEFAULT_CAPTURE_REGIONS
        self._profiles = profiles if profiles is not None else CAMP_COLOR_PROFILES
        self._game_time_provider = game_time_provider
        self._detector = CampTransitionDetector()

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._enabled = False

        # Public counters — read by diagnostics. Atomic writes via the
        # GIL; no lock needed for monotonic int counters.
        self.frames_processed = 0
        self.events_emitted = 0
        self.failures = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Spin up the worker thread. No-op if already running, or if
        the capture instance is disabled (non-Windows / mss missing /
        permission denied at construction)."""
        if self._thread is not None and self._thread.is_alive():
            return
        if self._capture is None:
            from .capture import MinimapCapture
            self._capture = MinimapCapture()
        if not self._capture.enabled:
            logger.info("[VISION] not started: capture disabled")
            return

        self._enabled = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="vision-observation", daemon=True,
        )
        self._thread.start()
        logger.info("[VISION] service started")

    def stop(self) -> None:
        """Graceful shutdown — signal the worker, wait up to 100ms.
        Lifecycle contract: must return within < 100 ms."""
        if not self._enabled:
            return
        self._enabled = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.1)
        if self._capture is not None:
            self._capture.close()
        logger.info("[VISION] service stopped")

    # -- worker -----------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                cycle_start = time.monotonic()
                try:
                    self._cycle()
                except Exception:  # noqa: BLE001 — never let the loop die
                    logger.exception("[VISION] cycle crashed (continuing)")
                    self.failures += 1

                # Self-disable threshold: the capture's own failure
                # counter dominates — once it hits MAX, we stop.
                if (
                    self._capture is not None
                    and self._capture.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                ):
                    logger.warning(
                        "[VISION] disabled after repeated errors "
                        "(failures=%d)", self._capture.consecutive_failures,
                    )
                    return

                # Sleep precisely to the next interval boundary.
                elapsed = time.monotonic() - cycle_start
                remaining = LOOP_INTERVAL_S - elapsed
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            self._enabled = False

    def _cycle(self) -> None:
        if self._capture is None:
            return

        for camp_id, region in self._regions.items():
            profile = self._profiles.get(camp_id)
            if profile is None:
                continue

            image = self._capture.capture_region(region)
            self.frames_processed += 1
            if image is None:
                self.failures += 1
                continue

            visible, _ = detect_presence(image, profile)

            # Always emit the per-frame visibility event for telemetry —
            # but bounded by transition logic, so it's not spammy.
            event = self._detector.process(camp_id, visible)
            if event is None:
                continue

            self._on_clear_event(event)

    def _on_clear_event(self, event: CampClearedEvent) -> None:
        """Forward a confirmed clear to the engine + telemetry."""
        self.events_emitted += 1
        try:
            telemetry.recorder().record(
                "camp_clear_inferred",
                {
                    "camp_id": event.camp_id,
                    "confidence": event.confidence,
                    "source": "color_heuristic",
                },
            )
        except Exception:  # noqa: BLE001 — telemetry must never break vision
            pass

        # Resolve the engine-side anchor in game-time, not wall-clock.
        # If we don't have a game-time provider we still emit but with
        # 0.0 — the engine handles invalid anchors silently.
        gt = 0.0
        if self._game_time_provider is not None:
            try:
                provided = self._game_time_provider()
                if isinstance(provided, (int, float)):
                    gt = float(provided)
            except Exception:  # noqa: BLE001
                pass
        self.camp_cleared.emit(event.camp_id, gt, event.confidence)
