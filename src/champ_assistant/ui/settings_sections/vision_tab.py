"""Vision tab — experimental Windows-only color-heuristic detectors."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QLabel, QWidget

from .. import styles
from ._helpers import checkbox, hint_label, scrolling_page, section_header, vertical

if TYPE_CHECKING:
    from ..settings_dialog import SettingsDialog


def build_vision_tab(dlg: "SettingsDialog") -> QWidget:
    """Both checkboxes are Windows-only and require capture-region
    calibration; honest warning at the top of the tab.

    Mutates ``dlg`` to set ``_cb_auto_camp``, ``_cb_scoreboard_detect``."""
    page = scrolling_page()
    body = vertical(page)

    warning = QLabel(
        "⚠ Experimentell — funktioniert nur unter Windows und "
        "benötigt Kalibrierung der Capture-Region für deine "
        "Auflösung. Defaults zielen auf 1080p mit Standard-Minimap."
    )
    warning.setStyleSheet(
        f"color: {styles.WARNING}; font-size: {styles.FS_LABEL}px;"
        f" background: {styles.BG_TERTIARY};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS_SMALL}px;"
        " padding: 8px 10px;"
    )
    warning.setWordWrap(True)
    body.addWidget(warning)

    body.addWidget(section_header("Camp Detection"))
    dlg._cb_auto_camp = checkbox(
        "Auto-Detect Jungle Camps (color heuristic)",
        dlg._display_state.enable_auto_camp_detection,
    )
    body.addWidget(dlg._cb_auto_camp)
    body.addWidget(hint_label(
        "Setzt den Engine-Anchor wenn ein Camp-Icon aus der "
        "Minimap verschwindet. Stage A: einfache Farb-Heuristik. "
        "Bias zu false-negative — verpasste Clears lassen den "
        "deterministischen Zyklus unverändert."
    ))

    body.addWidget(section_header("Scoreboard Detection"))
    dlg._cb_scoreboard_detect = checkbox(
        "Auto-Detect Scoreboard (TAB-Anzeige)",
        dlg._display_state.enable_scoreboard_detection,
    )
    body.addWidget(dlg._cb_scoreboard_detect)
    body.addWidget(hint_label(
        "Erkennt das in-game Scoreboard via "
        "low-variance + dark-pixel Heuristik im Top-Center-Bereich. "
        "Triggert die Gold-Diff-Anzeige während TAB gehalten wird. "
        "Manuelle Alternative: Ctrl+Alt+B."
    ))
    body.addStretch(1)
    return page
