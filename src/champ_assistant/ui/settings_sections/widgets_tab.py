"""Widgets tab — main panel + floating widget visibility + reset layout."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from .. import styles
from ._helpers import (
    checkbox,
    flat_button,
    hint_label,
    scrolling_page,
    section_header,
    vertical,
)

if TYPE_CHECKING:
    from ..settings_dialog import SettingsDialog


def build_widgets_tab(dlg: "SettingsDialog") -> QWidget:
    """Visibility toggles for both main-overlay sections AND the
    floating widgets, plus a layout-reset action.

    Mutates ``dlg`` to set ``_cb_summoners``, ``_cb_spikes``,
    ``_cb_scoreboard``, ``_cb_minimap``."""
    page = scrolling_page()
    body = vertical(page)

    body.addWidget(section_header("Main Overlay Sections"))
    dlg._cb_summoners = checkbox(
        "Show Summoner Tracker (enemy spell cooldowns)",
        dlg._display_state.show_summoners,
    )
    dlg._cb_spikes = checkbox(
        "Show Power-Spike panel",
        dlg._display_state.show_spikes,
    )
    for cb in (dlg._cb_summoners, dlg._cb_spikes):
        body.addWidget(cb)

    body.addWidget(section_header("Floating Mini-Widgets"))
    dlg._cb_scoreboard = checkbox(
        "Scoreboard widget (kills + gold delta + objectives)",
        dlg._display_state.show_scoreboard,
    )
    dlg._cb_minimap = checkbox(
        "Minimap-Timers widget (Dragon / Baron / camp predictions)",
        dlg._display_state.show_minimap_timers,
    )
    for cb in (dlg._cb_scoreboard, dlg._cb_minimap):
        body.addWidget(cb)

    body.addWidget(section_header("Layout"))
    reset_row = QHBoxLayout()
    reset_label = QLabel("Reset widget positions to defaults")
    reset_label.setStyleSheet(
        f"color: {styles.TEXT_PRIMARY}; font-size: {styles.FS_BODY}px;"
    )
    reset_row.addWidget(reset_label, 1)
    reset_btn = flat_button("Reset Layout")
    reset_btn.clicked.connect(dlg._on_reset_layout)
    reset_row.addWidget(reset_btn)
    body.addLayout(reset_row)

    body.addWidget(hint_label(
        "Widget visibility changes apply on the next launch. "
        "Reset Layout takes effect immediately."
    ))
    body.addStretch(1)
    return page
