"""Tests for the window-flag audit helpers."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMainWindow

from champ_assistant.window_flags import (
    apply_champselect_flags,
    apply_overlay_flags,
    set_passthrough,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_overlay_flags_set_all_required(app) -> None:  # type: ignore[no-untyped-def]
    w = QMainWindow()
    apply_overlay_flags(w)
    flags = w.windowFlags()
    # Hint flags can be tested via bitwise AND.
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    # The window-type field is multi-bit (Tool == Window|0x10) so use
    # windowType() which masks out hint bits.
    assert w.windowType() == Qt.WindowType.Tool
    assert w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_overlay_flags_idempotent(app) -> None:  # type: ignore[no-untyped-def]
    w = QMainWindow()
    apply_overlay_flags(w)
    first = int(w.windowFlags())
    apply_overlay_flags(w)
    apply_overlay_flags(w)
    assert int(w.windowFlags()) == first


def test_champselect_flags_strip_overlay_bits(app) -> None:  # type: ignore[no-untyped-def]
    w = QMainWindow()
    apply_overlay_flags(w)
    apply_champselect_flags(w)
    flags = w.windowFlags()
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)
    assert not (flags & Qt.WindowType.WindowDoesNotAcceptFocus)
    # Window-type field demoted from Tool back to plain Window.
    assert w.windowType() == Qt.WindowType.Window
    assert not w.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_set_passthrough_toggles_attribute(app) -> None:  # type: ignore[no-untyped-def]
    w = QMainWindow()
    set_passthrough(w, True)
    assert w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    set_passthrough(w, False)
    assert not w.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
