"""Single source of truth for overlay window flags.

Two callable helpers — :func:`apply_overlay_flags` and
:func:`apply_champselect_flags` — that set every Qt flag and widget
attribute the corresponding mode requires. Both are idempotent: calling
them multiple times yields the same window state.

Why this matters: Qt's ``setWindowFlags`` *replaces* the flag word and
some flags like ``Tool`` are multi-bit (``Tool`` = ``Window | 0x10``),
so naive OR/AND-NOT corrupts other Window bits. We use the per-flag
``setWindowFlag(flag, on)`` API which Qt6 introduced specifically to
avoid that trap.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


# Bool-keyed table of every flag this layer manages, indexed by the mode
# that wants it ON. apply_*_flags() iterate through both modes' tables to
# produce a deterministic result regardless of prior state.
_OVERLAY_FLAGS: dict[Qt.WindowType, bool] = {
    Qt.WindowType.FramelessWindowHint:      True,
    Qt.WindowType.WindowStaysOnTopHint:     True,
    Qt.WindowType.Tool:                     True,
    Qt.WindowType.WindowDoesNotAcceptFocus: True,
}

_CHAMPSELECT_FLAGS: dict[Qt.WindowType, bool] = {
    Qt.WindowType.FramelessWindowHint:      True,
    Qt.WindowType.WindowStaysOnTopHint:     False,
    Qt.WindowType.Tool:                     False,
    Qt.WindowType.WindowDoesNotAcceptFocus: False,
}


def _apply(window: QWidget, table: dict[Qt.WindowType, bool]) -> None:
    for flag, on in table.items():
        window.setWindowFlag(flag, on)


def apply_overlay_flags(window: QWidget) -> None:
    """Configure ``window`` for in-game overlay mode.

    Idempotent — calling repeatedly leaves the same flag word.
    The window is hidden + reshown by Qt's flag-change semantics; callers
    that had it visible should re-show after this returns.
    """
    _apply(window, _OVERLAY_FLAGS)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    logger.debug("apply_overlay_flags applied")


def apply_champselect_flags(window: QWidget) -> None:
    """Configure ``window`` for the wide champ-select layout.

    Strips the topmost / tool / no-focus flags so the user can Alt+Tab
    freely to LeagueClient and back.
    """
    _apply(window, _CHAMPSELECT_FLAGS)
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
    logger.debug("apply_champselect_flags applied")


def set_passthrough(widget: QWidget, on: bool) -> None:
    """Toggle click-through on ``widget``. Idempotent.

    When ``on``: every mouse event bypasses the widget so the game gets
    the click. When ``off``: drag/right-click work normally.
    """
    widget.setAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents, bool(on)
    )
