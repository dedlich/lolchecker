"""Loading-screen roster — separate top-level window.

When the game is starting (LCU phase ``GAME_STARTING`` →
``display_subphase() == "loading"``) the user is on the loading
screen reading roster info, not making champ-select decisions. The
RosterWindow is the dedicated surface for that moment: a separate
frameless top-level window that shows ALL 10 players' mains / WR /
streak. The main LiveCompanion window hides during this phase to
keep the user focused.

v1.10.110: split the RosterPanel out of LiveCompanionView's body.
The previous embedded layout meant during finalization + loading the
roster strip rendered below the 3-column champ-select body — busy,
and the champ-select UI was no longer relevant.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from . import styles
from .floating_widget import FloatingWidget
from .live_companion_sections.roster_panel import RosterPanel

if TYPE_CHECKING:
    from PyQt6.QtGui import QPixmap

    from .view_model import SessionView

IconLookup = Callable[[str], "QPixmap | None"]


class RosterWindow(FloatingWidget):
    """Standalone roster window for the loading-screen phase.

    Inherits FloatingWidget so it gets the standard frameless +
    always-on-top + drop-shadow + position-persistence behavior. The
    only champ-select-related widget that lives outside
    LiveCompanionView's tree.
    """

    KEY = "roster_window"
    DEFAULT_POS = (200, 120)
    DEFAULT_SIZE = (520, 460)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(420, 360)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_LOOSE, styles.SPACING_LOOSE,
            styles.SPACING_LOOSE, styles.SPACING_LOOSE,
        )
        outer.setSpacing(styles.SPACING_GRID)

        title = QLabel("Loading Screen — Roster")
        title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_TITLE}px;"
            " font-weight: 700; letter-spacing: -0.2px;"
        )
        title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(title)

        subtitle = QLabel(
            "Mains · win-rate · recent form for both teams"
        )
        subtitle.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " letter-spacing: 0.4px;"
        )
        outer.addWidget(subtitle)

        self._roster = RosterPanel()
        outer.addWidget(self._roster)
        outer.addStretch(1)

        self.hide()

    def update_view(self, view: "SessionView", icon_lookup: IconLookup) -> None:
        """Show during loading-screen subphase, hide otherwise. Roster
        rows always re-render so the window has fresh data the moment
        the user un-hides via subphase transition."""
        session = view.session
        subphase = session.display_subphase() if session is not None else "idle"
        if subphase == "loading":
            self._roster.update_panel(view, icon_lookup)
            if not self.isVisible():
                self.show()
        else:
            if self.isVisible():
                self.hide()
