"""Ban-suggestion column inside Live Companion's left panel.

Two stacked sub-sections (Lane Bans / All-round Bans), each rendering up
to 3 ``view.ban_suggestions_lane`` / ``view.ban_suggestions_allround``
entries as clickable rows. Clicking a row hovers the champ in the
player's ban slot via ``ban_hover_requested``.

Used to live as a separate ``BanPanel`` parented to ``MainOverlay``;
absorbed into Live Companion in v1.10.82 so the legacy stack disappears.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .. import styles

if TYPE_CHECKING:
    from ...advisor.ban_suggestions import BanSuggestion
    from ..view_model import SessionView

IconLookup = Callable[[str], "QPixmap | None"]


class _ClickableRow(QFrame):
    """Row that fires ``on_click(champion_key)`` on a left mouse press."""

    def __init__(self, champion_key: str, on_click: Callable[[str], None]) -> None:
        super().__init__()
        self._key = champion_key
        self._on_click = on_click
        self.setProperty("role", "row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # type: ignore[override]
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self._key)
        super().mousePressEvent(event)


class BansColumn(QWidget):
    """Ban suggestion list inside the left LiveCompanion column."""

    ban_hover_requested = pyqtSignal(str)

    MAX_ROWS_PER_SECTION = 3
    ICON_SIZE_PX = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(styles.SPACING_TIGHT)

        layout.addWidget(self._section_label("Ban Picks"))
        self._lane_col = QVBoxLayout()
        self._lane_col.setSpacing(styles.SPACING_TIGHT)
        layout.addLayout(self._lane_col)

        self._empty_state = QLabel("Bans appear during the ban phase.")
        self._empty_state.setWordWrap(True)
        self._empty_state.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_CAPTION}px;"
            " font-style: italic;"
        )
        layout.addWidget(self._empty_state)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; letter-spacing: 0.5px;"
        )
        return label

    def update_bans(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> None:
        self._clear(self._lane_col)
        # Prefer lane-specific bans; fall back to all-round when lane is empty.
        bans = view.ban_suggestions_lane[: self.MAX_ROWS_PER_SECTION]
        if not bans:
            bans = view.ban_suggestions_allround[: self.MAX_ROWS_PER_SECTION]
        if not bans:
            self._empty_state.show()
            return
        self._empty_state.hide()
        for s in bans:
            self._lane_col.addWidget(self._row(s, icon_lookup))

    def _row(
        self,
        suggestion: "BanSuggestion",
        icon_lookup: IconLookup,
    ) -> _ClickableRow:
        row = _ClickableRow(
            suggestion.champion_key,
            lambda key: self.ban_hover_requested.emit(key),
        )
        row.setStyleSheet(
            f"QFrame[role='row'] {{ background-color: {styles.BG_TERTIARY};"
            f" border-radius: {styles.RADIUS}px;"
            f" border-left: 3px solid {styles.DANGER}; }}"
            f" QFrame[role='row']:hover {{ background-color: {styles.BG_INTERACT}; }}"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        icon = QLabel()
        icon.setFixedSize(self.ICON_SIZE_PX, self.ICON_SIZE_PX)
        icon.setScaledContents(True)
        icon.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
        )
        pix = icon_lookup(suggestion.champion_key)
        if pix is not None and not pix.isNull():
            icon.setPixmap(pix)
        h.addWidget(icon)

        name = QLabel(suggestion.champion_key)
        name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_BODY}px; font-weight: 700;"
        )
        h.addWidget(name, 1)

        score = QLabel(f"{int(round(suggestion.score))}")
        score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        score.setStyleSheet(
            f"color: {styles.DANGER};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_BODY}px; font-weight: 700;"
        )
        score.setFixedWidth(32)
        h.addWidget(score)
        return row

    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
