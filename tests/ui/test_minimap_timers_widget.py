"""Smoke tests for the rewritten MinimapTimersWidget.

The widget went from a multi-row floating panel to a transparent
overlay positioned over the in-game minimap. These tests validate
the new shape: square, transparent, attaches to a JungleTimelineEngine,
auto-resizes the inner MapOverlayLayer, and forwards LCDA objective
state.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from champ_assistant.jungle_timeline import JungleTimelineEngine
from champ_assistant.ui.map_overlay_layer import MapOverlayLayer
from champ_assistant.ui.minimap_timers_widget import MinimapTimersWidget


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


def test_widget_is_transparent_and_frameless(qt_app) -> None:
    """Translucent background + frameless flags so the layer paints
    on top of whatever's behind the widget (the in-game minimap)."""
    w = MinimapTimersWidget()
    assert w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    flags = w.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_widget_is_square_by_default(qt_app) -> None:
    """Minimap is square in-game; widget matches."""
    w = MinimapTimersWidget()
    width, height = w.DEFAULT_SIZE
    assert width == height


def test_attach_engine_creates_inner_layer(qt_app) -> None:
    w = MinimapTimersWidget()
    engine = JungleTimelineEngine()
    w.attach_engine(engine)
    assert w._map_layer is not None
    assert isinstance(w._map_layer, MapOverlayLayer)
    # Layer fills the widget area so click-to-arm works across the
    # entire minimap region.
    assert w._map_layer.geometry() == w.rect()


def test_resize_propagates_to_layer(qt_app) -> None:
    """User (or auto-pin) resizes the widget → inner layer matches."""
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QResizeEvent

    w = MinimapTimersWidget()
    engine = JungleTimelineEngine()
    w.attach_engine(engine)
    w.resize(400, 400)
    # Offscreen Qt doesn't always deliver resizeEvent synchronously —
    # invoke directly so the test verifies the propagation logic
    # rather than the platform's event delivery timing.
    w.resizeEvent(QResizeEvent(QSize(400, 400), QSize(*w.DEFAULT_SIZE)))
    assert w._map_layer is not None
    assert w._map_layer.width() == 400
    assert w._map_layer.height() == 400


def test_update_snapshot_forwards_objectives(qt_app) -> None:
    """LCDA snapshot → objective state lands in the layer so D/B/H
    markers can render their countdown."""
    from champ_assistant.lcda.objectives import ObjectiveTimer

    w = MinimapTimersWidget()
    engine = JungleTimelineEngine()
    w.attach_engine(engine)

    class _FakeSnapshot:
        game_time = 600.0
        objectives = [
            ObjectiveTimer(
                name="Dragon",
                next_spawn_seconds=900.0,
                last_killed_seconds=600.0,
            ),
        ]

    w.update_snapshot(_FakeSnapshot())
    assert "Dragon" in w._map_layer._objectives
    assert w._map_layer._objective_game_time == 600.0


def test_update_snapshot_none_hides_widget(qt_app) -> None:
    w = MinimapTimersWidget()
    w.show()
    w.update_snapshot(None)
    assert not w.isVisible()
