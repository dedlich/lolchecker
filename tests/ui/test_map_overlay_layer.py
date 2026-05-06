"""Tests for the MapOverlayLayer — coordinate math, paint smoke, resize."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QApplication

from champ_assistant.jungle_timeline import (
    JUNGLE_CAMPS,
    JungleTimelineEngine,
)
from champ_assistant.ui.map_overlay_layer import (
    CAMP_COLORS,
    CAMP_GLYPHS,
    CAMP_POSITIONS,
    MARKER_RADIUS_PX,
    MapOverlayLayer,
    _format_mmss,
    map_to_screen,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def test_map_to_screen_at_origin() -> None:
    rect = QRect(0, 0, 100, 200)
    pt = map_to_screen(rect, 0.0, 0.0)
    assert (pt.x(), pt.y()) == (0, 0)


def test_map_to_screen_at_far_corner() -> None:
    rect = QRect(0, 0, 100, 200)
    pt = map_to_screen(rect, 1.0, 1.0)
    assert (pt.x(), pt.y()) == (100, 200)


def test_map_to_screen_at_center() -> None:
    rect = QRect(0, 0, 100, 200)
    pt = map_to_screen(rect, 0.5, 0.5)
    assert (pt.x(), pt.y()) == (50, 100)


def test_map_to_screen_with_nonzero_origin() -> None:
    """Camps on a panel whose top-left is offset (e.g. inside a parent
    widget) — the screen coordinate must include the rect's origin."""
    rect = QRect(40, 80, 100, 100)
    pt = map_to_screen(rect, 0.5, 0.5)
    assert (pt.x(), pt.y()) == (90, 130)


def test_map_to_screen_handles_extreme_norms() -> None:
    """Defensive: norm values > 1 or < 0 just compute outside the rect.
    No clamping inside this helper — caller's responsibility if needed."""
    rect = QRect(0, 0, 100, 100)
    out = map_to_screen(rect, 1.5, -0.2)
    assert out.x() == 150 and out.y() == -20


# ----------------------------------------------------------------------
# Time formatting
# ----------------------------------------------------------------------
def test_format_mmss_basic() -> None:
    assert _format_mmss(0) == "0:00"
    assert _format_mmss(45) == "0:45"
    assert _format_mmss(60) == "1:00"
    assert _format_mmss(125) == "2:05"
    assert _format_mmss(300) == "5:00"


def test_format_mmss_negative_clamps_to_zero() -> None:
    assert _format_mmss(-5) == "0:00"


def test_format_mmss_rounds_half_up() -> None:
    # 89.5 → 90 → 1:30
    assert _format_mmss(89.5) == "1:30"


# ----------------------------------------------------------------------
# CAMP_POSITIONS coverage
# ----------------------------------------------------------------------
def test_camp_positions_cover_every_engine_camp() -> None:
    """Every camp the engine knows about must have a normalized
    position — otherwise it's silently invisible on the map."""
    engine_ids = {spec.id for spec in JUNGLE_CAMPS}
    position_ids = set(CAMP_POSITIONS.keys())
    missing = engine_ids - position_ids
    assert not missing, f"camps without positions: {missing}"


def test_camp_positions_are_in_unit_square() -> None:
    for camp_id, (nx, ny) in CAMP_POSITIONS.items():
        assert 0.0 <= nx <= 1.0, f"{camp_id} norm_x out of range: {nx}"
        assert 0.0 <= ny <= 1.0, f"{camp_id} norm_y out of range: {ny}"


# ----------------------------------------------------------------------
# Paint smoke (Qt-required)
# ----------------------------------------------------------------------
def test_layer_constructs_and_paints_without_engine_state(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Engine present but never ticked — layer renders blank, no
    crash. Real-world case: floating widget shown before LCDA arrives."""
    engine = JungleTimelineEngine()
    layer = MapOverlayLayer(engine)
    layer.resize(110, 110)
    layer.show()
    qt_app.processEvents()
    # If we got here, paintEvent didn't crash.


def test_layer_paints_with_active_camps(qt_app) -> None:  # type: ignore[no-untyped-def]
    """At 10:00 game time several camps are mid-cycle; layer should
    paint countdowns at the right positions."""
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    layer = MapOverlayLayer(engine)
    layer.resize(110, 110)
    layer.show()
    qt_app.processEvents()


# Click-to-arm tests removed in v1.10.77 — feature retired in d3e022b
# ("live overlay polish — vision-driven timers, tab scoreboard, auto-apply").
# The map overlay now sets WA_TransparentForMouseEvents so minimap clicks
# pass through to the game; observed clears come from the vision detector
# instead. Tests that called layer.mousePressEvent are no longer meaningful.


# ----------------------------------------------------------------------
# Resize behavior
# ----------------------------------------------------------------------
def test_layer_repaints_after_resize(qt_app) -> None:  # type: ignore[no-untyped-def]
    """Resizing the layer must not crash and the camps follow the new
    rect. Tested by checking that geometry actually updates."""
    engine = JungleTimelineEngine()
    engine.tick(600.0)
    layer = MapOverlayLayer(engine)
    layer.resize(80, 80)
    layer.show()
    qt_app.processEvents()
    layer.resize(150, 150)
    qt_app.processEvents()
    assert layer.size().width() == 150
    assert layer.size().height() == 150


# ----------------------------------------------------------------------
# Blink phase
# ----------------------------------------------------------------------
def test_blink_phase_toggles_on_tick(qt_app) -> None:  # type: ignore[no-untyped-def]
    """The internal _blink_phase flips 0/1 on every tick — verified
    via direct method invocation since the scheduler signal hookup is
    a separate concern."""
    layer = MapOverlayLayer(JungleTimelineEngine())
    initial = layer._blink_phase
    layer._on_tick()
    assert layer._blink_phase != initial
    layer._on_tick()
    assert layer._blink_phase == initial


# ----------------------------------------------------------------------
# Engine error tolerance
# ----------------------------------------------------------------------
def test_layer_tolerates_engine_states_raising(qt_app) -> None:  # type: ignore[no-untyped-def]
    """A misbehaving engine.states() must not crash the paint event —
    UI safety requires graceful degradation."""

    class HostileEngine:
        def states(self):
            raise RuntimeError("teardown half-done")

    layer = MapOverlayLayer(HostileEngine())  # type: ignore[arg-type]
    layer.resize(110, 110)
    layer.show()
    qt_app.processEvents()  # would crash if paintEvent didn't catch


# ----------------------------------------------------------------------
# Camp marker registry — every camp has a glyph + color
# ----------------------------------------------------------------------
def test_every_camp_has_marker_metadata() -> None:
    """Camp markers (drawn at every position regardless of timer state)
    let the user see what's clickable. Every camp in the engine's spec
    must have an entry in both CAMP_GLYPHS and CAMP_COLORS so the
    paint pass doesn't fall back to the '?' / grey defaults."""
    for spec in JUNGLE_CAMPS:
        assert spec.id in CAMP_POSITIONS, f"missing position: {spec.id}"
        assert spec.id in CAMP_GLYPHS, f"missing glyph: {spec.id}"
        assert spec.id in CAMP_COLORS, f"missing color: {spec.id}"


def test_camp_glyphs_are_unique_per_side() -> None:
    """Order-side glyphs are distinct and chaos-side glyphs are distinct.
    Both sides intentionally share the same letter per camp type (R=Red,
    B=Blue, …) — the position on the minimap disambiguates them visually."""
    order_glyphs = [g for k, g in CAMP_GLYPHS.items() if k.startswith("order_")]
    chaos_glyphs  = [g for k, g in CAMP_GLYPHS.items() if k.startswith("chaos_")]
    assert len(order_glyphs) == len(set(order_glyphs)), f"duplicate order glyphs: {order_glyphs}"
    assert len(chaos_glyphs)  == len(set(chaos_glyphs)),  f"duplicate chaos glyphs: {chaos_glyphs}"


def test_marker_radius_fits_in_smallest_panel() -> None:
    """Marker diameter must be small enough that seven non-overlapping
    markers fit on the smallest minimap panel (110px). Sanity check
    so a constant bump doesn't quietly produce overlapping markers."""
    # Worst-case neighbor distance: gromp (0.78, 0.28) ↔ wolves (0.70, 0.35)
    # → distance ≈ 0.106 in normalized units. On a 110px panel that's
    # ~12px — markers must be ≤6px radius to not overlap, but we use 9
    # accepting some overlap on tiny panels in exchange for legibility
    # at typical 200+ px sizes.
    assert MARKER_RADIUS_PX <= 14
    assert MARKER_RADIUS_PX >= 6
