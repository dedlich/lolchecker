"""ScoreboardVisibilityService — vision worker that pushes
scoreboard_visible into the StateStore.

Architecture
============
Independent worker thread (separate from VisionObservationService for
the camp detection — different cadence, different region, cleaner
shutdown semantics). 500ms loop. Captures one small region at the top
of the screen, runs the dark+uniform heuristic, applies a 2-frame
state-machine before declaring a transition.

The service writes to ``state_store.scoreboard_visible`` via a Qt
signal connected with ``Qt.QueuedConnection`` so the state mutation
lands on the Qt main thread — same pattern as Stage A camp clears.

Failure budget: same MAX_CONSECUTIVE_FAILURES from Stage A's config.
After threshold the service self-disables; UI just permanently sees
``scoreboard_visible=False``, which is the safe default.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from .. import telemetry
from .config import LOOP_INTERVAL_S, MAX_CONSECUTIVE_FAILURES, CaptureRegion
from .scoreboard_detector import (
    ScoreboardPresenceDetector,
    ScoreboardThresholds,
)

if TYPE_CHECKING:
    from .capture import MinimapCapture

logger = logging.getLogger(__name__)


# Default capture region — placeholder for 1080p. Top-center horizontal
# strip, narrow vertical band where the scoreboard's title bar sits.
# 200×40 px is enough to compute a stable mean+variance without paying
# for a full screen grab.
DEFAULT_SCOREBOARD_REGION = CaptureRegion(
    left=860, top=20, width=200, height=40,
)

# 2-frame stability requirement matches Stage A's camp transition
# detector — same noise-rejection rationale.
CONFIRM_FRAMES = 2


class ScoreboardVisibilityService(QObject):
    """Detect scoreboard show/hide via vision and push state into the
    StateStore. Single instance per session; thread-safe via Qt
    signal queueing.
    """

    # Connected to a slot on the main thread that calls
    # ``state_store.update(scoreboard_visible=...)``.
    visibility_changed = pyqtSignal(bool)

    def __init__(
        self,
        *,
        capture: "MinimapCapture | None" = None,
        region: CaptureRegion = DEFAULT_SCOREBOARD_REGION,
        thresholds: ScoreboardThresholds = ScoreboardThresholds(),
    ) -> None:
        super().__init__()
        self._capture = capture
        self._region = region
        self._detector = ScoreboardPresenceDetector(thresholds)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._enabled = False

        # State machine: track last confirmed visibility + how many
        # consecutive frames matched the candidate state before a
        # transition was accepted.
        self._confirmed_visible = False
        self._candidate_visible: bool | None = None
        self._candidate_count = 0

        # Diagnostics counters — read by the [DIAG] line same as
        # camp-detection counters.
        self.frames_processed = 0
        self.transitions_emitted = 0
        self.failures = 0

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._capture is None:
            from .capture import MinimapCapture
            self._capture = MinimapCapture()
        if not self._capture.enabled:
            logger.info("[VISION_SCOREBOARD] not started: capture disabled")
            return

        self._enabled = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="vision-scoreboard",
            daemon=True,
        )
        self._thread.start()
        logger.info("[VISION_SCOREBOARD] service started")

    def stop(self) -> None:
        if not self._enabled:
            return
        self._enabled = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.1)
        if self._capture is not None:
            self._capture.close()
        logger.info("[VISION_SCOREBOARD] service stopped")

    # -- worker -----------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                cycle_start = time.monotonic()
                try:
                    self._cycle()
                except Exception:  # noqa: BLE001
                    logger.exception("[VISION_SCOREBOARD] cycle crashed (continuing)")
                    self.failures += 1

                if (
                    self._capture is not None
                    and self._capture.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                ):
                    logger.warning(
                        "[VISION_SCOREBOARD] disabled after repeated errors "
                        "(failures=%d)", self._capture.consecutive_failures,
                    )
                    return

                elapsed = time.monotonic() - cycle_start
                remaining = LOOP_INTERVAL_S - elapsed
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            self._enabled = False

    def _cycle(self) -> None:
        if self._capture is None:
            return
        image = self._capture.capture_region(self._region)
        self.frames_processed += 1
        if image is None:
            self.failures += 1
            return

        present = self._detector.detect(image)
        self._process_frame_verdict(present)

    def _process_frame_verdict(self, frame_visible: bool) -> None:
        """Apply 2-frame stability filter. Public-via-tests so the
        state machine can be exercised directly without a real
        capture pipeline."""
        # If the new frame matches the confirmed state, reset the
        # candidate counter — nothing to transition to.
        if frame_visible == self._confirmed_visible:
            self._candidate_visible = None
            self._candidate_count = 0
            return

        # New frame disagrees with confirmed state. Build up
        # consecutive-frame count for the candidate transition.
        if self._candidate_visible == frame_visible:
            self._candidate_count += 1
        else:
            self._candidate_visible = frame_visible
            self._candidate_count = 1

        if self._candidate_count < CONFIRM_FRAMES:
            return

        # 2 consecutive frames agree on the new state — commit.
        self._confirmed_visible = frame_visible
        self._candidate_visible = None
        self._candidate_count = 0
        self.transitions_emitted += 1

        # Telemetry first, signal second. Telemetry never throws; if
        # the signal connection has a bad slot Qt's event handling
        # logs but doesn't propagate the error to us.
        try:
            telemetry.recorder().record(
                telemetry.EV_SCOREBOARD_VISIBLE if frame_visible
                else telemetry.EV_SCOREBOARD_HIDDEN,
                {"source": "vision_heuristic"},
            )
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            "[VISION_SCOREBOARD] %s",
            "visible" if frame_visible else "hidden",
        )
        self.visibility_changed.emit(frame_visible)
