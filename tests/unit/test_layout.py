"""Tests for the persistent layout store + multi-monitor safety."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from champ_assistant import layout as layout_module
from champ_assistant.layout import (
    LayoutStore,
    WidgetLayout,
    safe_position_for,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tmp_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Redirect layout.json to a temp file and reset the singleton so
    each test gets a fresh store backed by an empty directory."""
    p = tmp_path / "layout.json"
    monkeypatch.setattr(layout_module, "layout_path", lambda: p)
    layout_module.reset_singleton_for_tests()
    # Block migration from overlay_config so tests don't import it.
    monkeypatch.setattr(
        LayoutStore, "_migrate_from_overlay_config", lambda self: None,
    )
    yield p
    layout_module.reset_singleton_for_tests()


# --------------------------------------------------------------------------
# (de)serialize + persistence
# --------------------------------------------------------------------------
def test_get_returns_none_when_empty(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    store = layout_module.store()
    assert store.get("anything") is None


def test_mark_then_flush_writes_to_disk(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    import json as _json
    store = layout_module.store()
    store.mark("scoreboard", WidgetLayout(x=100, y=200, monitor_id=r"\\.\DISPLAY1"))
    store.flush_now()
    assert tmp_layout.is_file()
    data = _json.loads(tmp_layout.read_text())
    assert data["widgets"]["scoreboard"]["x"] == 100
    assert data["widgets"]["scoreboard"]["y"] == 200
    assert data["widgets"]["scoreboard"]["monitor_id"] == r"\\.\DISPLAY1"


def test_load_restores_on_next_session(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    store = layout_module.store()
    store.mark("scoreboard", WidgetLayout(x=300, y=400, visible=False))
    store.flush_now()

    layout_module.reset_singleton_for_tests()
    fresh = layout_module.store()
    got = fresh.get("scoreboard")
    assert got is not None
    assert got.x == 300
    assert got.y == 400
    assert got.visible is False


def test_corrupt_file_falls_back_to_defaults(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    tmp_layout.write_text("{ this is not valid json")
    store = layout_module.store()
    assert store.all_layouts() == {}


def test_invalid_entry_skipped_other_entries_kept(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    tmp_layout.write_text(
        '{"widgets": {"good": {"x": 1, "y": 2}, "bad": {"x": "not an int"}}}'
    )
    store = layout_module.store()
    assert "good" in store.all_layouts()
    assert "bad" not in store.all_layouts()


def test_mark_noop_when_unchanged(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    store = layout_module.store()
    layout = WidgetLayout(x=10, y=20)
    store.mark("k", layout)
    store.flush_now()
    # Second mark with identical values must not flag dirty.
    store.mark("k", layout)
    assert store._dirty is False


def test_reset_deletes_file_and_clears_state(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    store = layout_module.store()
    store.mark("k", WidgetLayout(x=1, y=2))
    store.flush_now()
    assert tmp_layout.is_file()
    store.reset()
    assert not tmp_layout.is_file()
    assert store.get("k") is None


def test_flush_now_is_idempotent_when_clean(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    store = layout_module.store()
    store.flush_now()  # nothing to flush — should not crash
    assert not tmp_layout.exists()


# --------------------------------------------------------------------------
# safe_position_for: multi-monitor + clamping
# --------------------------------------------------------------------------
def _fake_screen(name: str, geo: QRect) -> MagicMock:
    s = MagicMock()
    s.name.return_value = name
    s.availableGeometry.return_value = geo
    return s


def test_no_saved_layout_returns_fallback(qt_app) -> None:  # type: ignore[no-untyped-def]
    pos = safe_position_for(
        None, fallback_pos=(50, 60), fallback_size=(100, 100),
    )
    assert pos == (50, 60)


def test_clamps_to_visible_bounds_when_x_too_large(qt_app) -> None:  # type: ignore[no-untyped-def]
    fake = _fake_screen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1080))
    saved = WidgetLayout(x=3000, y=200, monitor_id=r"\\.\DISPLAY1")
    with patch("champ_assistant.layout.QGuiApplication.screens", return_value=[fake]), \
         patch("champ_assistant.layout.QGuiApplication.primaryScreen", return_value=fake):
        x, y = safe_position_for(
            saved, fallback_pos=(0, 0), fallback_size=(120, 60),
        )
    assert x == 1920 - 120  # clamped right edge
    assert y == 200          # untouched


def test_falls_back_to_primary_when_monitor_missing(qt_app) -> None:  # type: ignore[no-untyped-def]
    primary = _fake_screen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1080))
    saved = WidgetLayout(x=100, y=100, monitor_id=r"\\.\DISPLAY7")
    with patch("champ_assistant.layout.QGuiApplication.screens", return_value=[primary]), \
         patch("champ_assistant.layout.QGuiApplication.primaryScreen", return_value=primary):
        pos = safe_position_for(
            saved, fallback_pos=(0, 0), fallback_size=(100, 100),
        )
    assert pos == (100, 100)


def test_negative_coordinate_clamped_to_screen_origin(qt_app) -> None:  # type: ignore[no-untyped-def]
    fake = _fake_screen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1080))
    saved = WidgetLayout(x=-500, y=-500, monitor_id=r"\\.\DISPLAY1")
    with patch("champ_assistant.layout.QGuiApplication.screens", return_value=[fake]), \
         patch("champ_assistant.layout.QGuiApplication.primaryScreen", return_value=fake):
        x, y = safe_position_for(
            saved, fallback_pos=(0, 0), fallback_size=(100, 100),
        )
    assert x == 0
    assert y == 0


# --------------------------------------------------------------------------
# Coordinate integrity (P3)
# --------------------------------------------------------------------------
def test_widget_layout_rejects_nan_coords() -> None:
    import math
    with pytest.raises(ValueError):
        WidgetLayout(x=int(0), y=math.nan)  # type: ignore[arg-type]


def test_widget_layout_rejects_extreme_coords() -> None:
    with pytest.raises(ValueError):
        WidgetLayout(x=999_999_999, y=0)


def test_widget_layout_rejects_bool_coords() -> None:
    # bool is an int subclass in Python — must be filtered explicitly.
    with pytest.raises(ValueError):
        WidgetLayout(x=True, y=0)  # type: ignore[arg-type]


def test_mark_silently_drops_setattr_bypass(qt_app, tmp_layout) -> None:  # type: ignore[no-untyped-def]
    """Belt-and-suspenders: even if a caller forces an out-of-bounds
    coord onto a frozen instance via object.__setattr__ (e.g. unpickle
    of a corrupt blob), ``mark()`` re-validates and refuses to persist."""
    store = layout_module.store()
    good = WidgetLayout(x=10, y=20)
    store.mark("k", good)
    bad = WidgetLayout(x=10, y=20)
    object.__setattr__(bad, "x", 10**9)  # bypasses __post_init__
    store.mark("k", bad)
    assert store.get("k") == good  # bad value rejected


def test_uses_correct_monitor_when_multi_screen(qt_app) -> None:  # type: ignore[no-untyped-def]
    primary = _fake_screen(r"\\.\DISPLAY1", QRect(0, 0, 1920, 1080))
    secondary = _fake_screen(r"\\.\DISPLAY2", QRect(1920, 0, 2560, 1440))
    saved = WidgetLayout(x=2400, y=200, monitor_id=r"\\.\DISPLAY2")
    with patch(
        "champ_assistant.layout.QGuiApplication.screens",
        return_value=[primary, secondary],
    ), patch(
        "champ_assistant.layout.QGuiApplication.primaryScreen",
        return_value=primary,
    ):
        x, y = safe_position_for(
            saved, fallback_pos=(0, 0), fallback_size=(100, 100),
        )
    # Saved position is on DISPLAY2 and within bounds — keep as is.
    assert (x, y) == (2400, 200)
