"""Pick-suggestion column inside Live Companion's left panel.

Two stacked sub-sections (Counter Picks / Synergy Picks), each rendering
up to 3 ``view.picks_counter`` / ``view.picks_synergy`` entries as
clickable rows. Clicking a row commits the suggestion (per the
click-to-lock policy from CONTINUATION.md) by emitting
``pick_hover_requested`` with the champion key — the overlay listens
and translates that into the LCU PATCH that hovers the champ in the
player's pick slot.

Used to live as ``_picks_row`` in ``ui/overlay.py``; absorbed into Live
Companion's left column in v1.10.81.
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
    from ...advisor.picks import PickSuggestion
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


class PicksColumn(QWidget):
    """Two-section pick suggestion list (counter + synergy).

    Empty state shows muted helper text. After the user has at least
    one enemy locked in, ``view.picks_counter`` and ``view.picks_synergy``
    populate up to 3 rows each.
    """

    pick_hover_requested = pyqtSignal(str)

    MAX_ROWS_PER_SECTION = 3
    ICON_SIZE_PX = 22

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(styles.SPACING_TIGHT)

        layout.addWidget(self._section_label("Counter Picks"))
        self._counter_col = QVBoxLayout()
        self._counter_col.setSpacing(styles.SPACING_TIGHT)
        layout.addLayout(self._counter_col)

        layout.addWidget(self._section_label("Synergy Picks"))
        self._synergy_col = QVBoxLayout()
        self._synergy_col.setSpacing(styles.SPACING_TIGHT)
        layout.addLayout(self._synergy_col)

        self._empty_state = QLabel("Picks appear once enemies lock in.")
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

    def update_picks(
        self,
        view: "SessionView",
        icon_lookup: IconLookup,
    ) -> None:
        self._clear(self._counter_col)
        self._clear(self._synergy_col)
        counter = view.picks_counter[: self.MAX_ROWS_PER_SECTION]
        synergy = view.picks_synergy[: self.MAX_ROWS_PER_SECTION]
        if not counter and not synergy:
            self._empty_state.show()
            return
        self._empty_state.hide()
        for s in counter:
            self._counter_col.addWidget(self._row(s, icon_lookup, styles.ACCENT))
        for s in synergy:
            self._synergy_col.addWidget(self._row(s, icon_lookup, styles.SUCCESS))

    def _row(
        self,
        suggestion: "PickSuggestion",
        icon_lookup: IconLookup,
        accent: str,
    ) -> _ClickableRow:
        row = _ClickableRow(
            suggestion.champion_key,
            lambda key: self.pick_hover_requested.emit(key),
        )
        row.setStyleSheet(
            f"QFrame[role='row'] {{ background-color: {styles.BG_TERTIARY};"
            f" border-radius: {styles.RADIUS}px;"
            f" border-left: 3px solid {accent}; }}"
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
            f"color: {accent};"
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
