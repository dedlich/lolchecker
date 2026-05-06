"""Visual regression snapshots for the canonical UI states.

Each test instantiates a widget with deterministic state, renders it
through one event-loop cycle so layouts settle, then compares the
structural snapshot (stylesheet + layout params + text + tree shape)
to a checked-in baseline.

To regenerate baselines after an intentional UI change:

    UPDATE_VISUAL_BASELINES=1 .venv/bin/python -m pytest tests/visual/

Then review the diff in ``tests/visual/baseline/*.json`` and commit.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication

from champ_assistant.jungle_timeline import (
    CampState,
    JungleTimelineEngine,
)
from champ_assistant.lcda.source import LcdaSource
from champ_assistant.ui.minimap_timers_widget import MinimapTimersWidget
from champ_assistant.ui.scoreboard_widget import ScoreboardWidget

from ._snapshot import assert_snapshot_matches, snapshot_widget

LCDA_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "lcda" / "allgamedata_midgame.json"
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def _settle(qt_app, widget) -> None:  # type: ignore[no-untyped-def]
    """Force layout finalization so the snapshot reflects the final
    parented + sized state. processEvents() is enough — we're not
    waiting on animations or async tasks."""
    widget.show()
    qt_app.processEvents()


def _force_engine_states(engine: JungleTimelineEngine, confidence: float) -> None:
    """Pin the engine's confidence to a deterministic value so the band
    classification (and therefore the cell stylesheets) doesn't drift
    based on real-time decay between test runs."""
    original = engine.states

    def _states_with_pinned_confidence() -> dict[str, CampState]:
        return {
            cid: CampState(
                id=st.id, name=st.name, state=st.state,
                next_spawn_at=st.next_spawn_at,
                time_remaining=st.time_remaining,
                confidence=confidence,
            )
            for cid, st in original().items()
        }

    engine.states = _states_with_pinned_confidence  # type: ignore[method-assign]


# ----------------------------------------------------------------------
# Canonical state #0: pre-first-spawn — every camp counts down to its
# initial appearance, exercises the ``game_time < spec.first_spawn_s``
# branch in _camp_state_at. Without this scenario the pre_spawn code
# path is never rendered into a snapshot.
# ----------------------------------------------------------------------
def test_minimap_timers_pre_spawn(qt_app) -> None:  # type: ignore[no-untyped-def]
    engine = JungleTimelineEngine()
    engine.tick(30.0)  # 0:30 — every camp still pre-first-spawn
    _force_engine_states(engine, confidence=0.95)

    widget = MinimapTimersWidget()
    widget.attach_engine(engine)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "minimap_timers_pre_spawn",
        snapshot_widget(widget),
    )


# ----------------------------------------------------------------------
# Canonical state #1: minimap timers at 10:00 game time, HIGH confidence
# Covers: in-game mid-objective state from the spec
# ----------------------------------------------------------------------
def test_minimap_timers_midgame_high_confidence(qt_app) -> None:  # type: ignore[no-untyped-def]
    engine = JungleTimelineEngine()
    engine.tick(600.0)  # 10:00 game time
    _force_engine_states(engine, confidence=0.95)

    widget = MinimapTimersWidget()
    widget.attach_engine(engine)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "minimap_timers_midgame_high",
        snapshot_widget(widget),
    )


# ----------------------------------------------------------------------
# Canonical state #2: same widget at LOW confidence — the approximate
# mode visual encoding (≈ prefix in cell text, reduced opacity in the
# stylesheet's rgba alpha values) must round-trip stably.
# ----------------------------------------------------------------------
def test_minimap_timers_low_confidence_approximate_mode(qt_app) -> None:  # type: ignore[no-untyped-def]
    engine = JungleTimelineEngine()
    engine.tick(2400.0)  # 40:00 game time
    _force_engine_states(engine, confidence=0.3)

    widget = MinimapTimersWidget()
    widget.attach_engine(engine)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "minimap_timers_low_confidence",
        snapshot_widget(widget),
    )


# ----------------------------------------------------------------------
# Canonical state #1b: midgame at MID confidence band (0.4–0.8).
# The OPACITY_MID = 0.85 stylesheet path was previously uncovered —
# any change to the muted-band rendering would have slipped through
# without this baseline.
# ----------------------------------------------------------------------
def test_minimap_timers_midgame_mid_confidence(qt_app) -> None:  # type: ignore[no-untyped-def]
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    _force_engine_states(engine, confidence=0.6)

    widget = MinimapTimersWidget()
    widget.attach_engine(engine)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "minimap_timers_midgame_mid",
        snapshot_widget(widget),
    )


# ----------------------------------------------------------------------
# Canonical state #2b: just-spawned camps (within the 5s alive-grace
# window). Exercises the _alive_style() code path so its stylesheet
# (which uses styles.SUCCESS) is part of the captured surface — without
# this scenario, a token tweak to SUCCESS would not be caught.
# ----------------------------------------------------------------------
def test_minimap_timers_just_spawned_alive(qt_app) -> None:  # type: ignore[no-untyped-def]
    engine = JungleTimelineEngine()
    # 91s — buffs and small camps first-spawn at 90s, so they're all
    # within the 5s alive window. Scuttle first-spawns at 195s and is
    # still pre-spawn — exercises both code paths in one snapshot.
    engine.tick(91.0)
    _force_engine_states(engine, confidence=0.95)

    widget = MinimapTimersWidget()
    widget.attach_engine(engine)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "minimap_timers_just_spawned_alive",
        snapshot_widget(widget),
    )


# ----------------------------------------------------------------------
# Canonical state #3: scoreboard with the midgame LCDA fixture
# ----------------------------------------------------------------------
def test_scoreboard_midgame_snapshot(qt_app) -> None:  # type: ignore[no-untyped-def]
    data = json.loads(LCDA_FIXTURE.read_text())
    src = LcdaSource(MagicMock(), lambda *_: None)
    snapshot = src._snapshot_from(data)

    widget = ScoreboardWidget()
    widget.update_snapshot(snapshot)
    _settle(qt_app, widget)

    assert_snapshot_matches(
        "scoreboard_midgame",
        snapshot_widget(widget),
    )


# Canonical state #4 (lobby-stats idle) was retired in v1.10.80 with
# LobbyStatsWidget itself; LiveCompanionView's team summary covers the
# same surface now and has its own coverage in the integration tests.

# ----------------------------------------------------------------------
# Canonical state #5: minimap timers idle (no LCDA snapshot pushed,
# so cells render glyph-only). Covers the "minimal idle overlay" case
# from the spec for the timer widget.
# ----------------------------------------------------------------------
def test_minimap_timers_idle(qt_app) -> None:  # type: ignore[no-untyped-def]
    widget = MinimapTimersWidget()
    # No engine attached — cells should render their idle stylesheet.
    _settle(qt_app, widget)
    assert_snapshot_matches("minimap_timers_idle", snapshot_widget(widget))
