"""Per-camp visibility-state machine + N-frame confirmation.

Why N-frame confirmation matters
=================================
Color-heuristic detection is noisy. A single low-saturation frame
(brief minimap occlusion by a champion icon, particle effect,
fog-of-war shadow) shouldn't be enough to flip "camp visible" → "camp
gone". Requiring two consecutive frames with the same verdict cuts
false-flips dramatically while only delaying real detection by 500ms
at our 2Hz capture rate.

State per camp: a small dataclass tracking the last-confirmed
visibility, the count of consecutive same-verdict frames, and the
last-emit timestamp (used to dedup events that arrive faster than the
camp's respawn cycle).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CONFIRM_FRAMES = 2


@dataclass(frozen=True)
class CampClearedEvent:
    """Emitted once when a camp transitions visible → not visible
    AND the not-visible state is confirmed across CONFIRM_FRAMES."""
    camp_id: str
    timestamp: float   # wall-clock seconds, time.time()
    confidence: float  # 1.0 if 2+ stable frames, 0.5 if exactly 1


@dataclass
class _CampState:
    """Internal per-camp state. Module-private."""
    previous_visible: bool | None = None
    stable_visible_count: int = 0
    stable_not_visible_count: int = 0
    last_emit_ts: float | None = None


class CampTransitionDetector:
    """Stream of (camp_id, visible_now) → optional CampClearedEvent.

    Single instance per session. Calls happen on the vision worker
    thread; all state is local to this object so no cross-thread
    locking is needed (the engine sync uses a queued Qt signal, see
    observation_service).
    """

    # Don't emit a second event for the same camp inside this window.
    # Real respawns are 2:15+; any "clear" in <30s is double-firing.
    DEDUP_WINDOW_S = 30.0

    def __init__(self) -> None:
        self._states: dict[str, _CampState] = {}

    def process(
        self,
        camp_id: str,
        visible_now: bool,
        *,
        now: float | None = None,
    ) -> CampClearedEvent | None:
        """Feed one frame's verdict for ``camp_id``. Returns an event
        only when the verdict crosses visible → not_visible AND the
        not-visible state has been seen for at least 2 frames.

        ``now`` is injectable so tests don't have to mock time.time.
        """
        ts = now if now is not None else time.time()
        st = self._states.get(camp_id)
        if st is None:
            st = _CampState()
            self._states[camp_id] = st

        if visible_now:
            st.stable_visible_count += 1
            st.stable_not_visible_count = 0
            # First-time-seen-visible: just record, don't emit.
            if st.previous_visible in (None, False):
                st.previous_visible = True
            return None

        # visible_now is False
        st.stable_not_visible_count += 1
        st.stable_visible_count = 0

        # Need a transition to fire — only count if we previously saw
        # the camp visible.
        if st.previous_visible is not True:
            st.previous_visible = False
            return None

        # Hold off until we see 2 consecutive not-visible frames.
        if st.stable_not_visible_count < CONFIRM_FRAMES:
            return None

        # Dedup: don't emit again inside the 30s window.
        if (
            st.last_emit_ts is not None
            and ts - st.last_emit_ts < self.DEDUP_WINDOW_S
        ):
            return None

        # Confirmed clear — flip state, emit, record dedup timestamp.
        st.previous_visible = False
        st.last_emit_ts = ts
        confidence = 1.0 if st.stable_not_visible_count >= 2 else 0.5
        logger.info("[VISION] clear inferred camp=%s confidence=%.1f", camp_id, confidence)
        return CampClearedEvent(
            camp_id=camp_id, timestamp=ts, confidence=confidence,
        )

    def reset(self, camp_id: str | None = None) -> None:
        """Drop state for one camp or all. Used when re-arming after
        engine.register_clear so we don't immediately re-fire."""
        if camp_id is None:
            self._states.clear()
        else:
            self._states.pop(camp_id, None)
