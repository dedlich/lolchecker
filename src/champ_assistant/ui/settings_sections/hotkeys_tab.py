"""Hotkeys tab — global hotkey bindings, click-to-rebind."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .. import styles
from ._helpers import (
    hint_label,
    hotkey_button_stylesheet,
    scrolling_page,
    section_header,
    vertical,
)

if TYPE_CHECKING:
    from ..settings_dialog import SettingsDialog


def build_hotkeys_tab(dlg: "SettingsDialog") -> QWidget:
    """Mutates ``dlg`` to set ``_hotkey_buttons`` (dict of action →
    ``QPushButton``)."""
    page = scrolling_page()
    body = vertical(page)

    body.addWidget(section_header("Global Hotkeys"))

    dlg._hotkey_buttons = {}
    for action, display_name in dlg.HOTKEY_ACTIONS:
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(display_name)
        label.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: {styles.FS_BODY}px;"
        )
        row.addWidget(label, 1)

        current = dlg._current_hotkey_label(action)
        btn = QPushButton(current)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumWidth(140)
        btn.setStyleSheet(hotkey_button_stylesheet())
        btn.clicked.connect(
            lambda _checked=False, a=action: dlg._capture_hotkey(a)
        )
        dlg._hotkey_buttons[action] = btn
        row.addWidget(btn)
        body.addLayout(row)

    body.addWidget(hint_label(
        "Klick auf einen Hotkey öffnet den Aufnahme-Dialog. "
        "Auf macOS / Linux laufen die globalen Hotkeys nicht "
        "(Win32 RegisterHotKey-only)."
    ))
    body.addStretch(1)
    return page
