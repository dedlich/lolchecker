"""Champ-select panel showing the top ban suggestions.

Visual hierarchy (revised):

  * Red left-border on the row carries the ban semantic — that's
    the visual signal the user reads first.
  * Rank prefix (#1 / #2 / #3) replaces the heavy red score-pill
    as the position indicator.
  * Score becomes a plain right-aligned mono label in danger color
    — no background/border pill. The earlier red-pill-on-red-border
    treatment was visual overload.

Hides itself when there are no suggestions (e.g. no Riot API key
configured AND no tier-list data — should be rare).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..advisor.ban_suggestions import BanSuggestion
from . import styles

ICON_SIZE = 28


class _BanRow(QFrame):
    ban_hover_requested = pyqtSignal(str)
    # (champion_key) — fired on left-click anywhere in the row.
    # Bubbled by BanPanel; the orchestrator translates it into an
    # LCU PATCH that hovers the champ in the player's ban slot.

    def __init__(
        self,
        suggestion: BanSuggestion,
        icon: QPixmap | None,
        *,
        rank: int | None = None,
    ) -> None:
        super().__init__()
        self.setProperty("role", "row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._champion_key = suggestion.champion_key
        # Override the inherited red left-border with the new
        # design-token rhythm — full rounded card, accent-glow on
        # hover. The DANGER semantic survives via the score color
        # + rank prefix tint.
        # Red accent strip lives ON the row's left edge as a
        # vertical bar, NOT as a stylesheet border (would conflict
        # with the global QFrame[role='row'] hover state). Keep
        # the inherited row stylesheet so hover-glow + radius
        # stay consistent across all panels.
        self.setStyleSheet(
            f"QFrame[role='row'] {{ background-color: {styles.BG_TERTIARY};"
            f" border-radius: {styles.RADIUS}px;"
            f" border-left: 3px solid {styles.DANGER}; }}"
            f" QFrame[role='row']:hover {{"
            f" background-color: {styles.BG_INTERACT};"
            f" border-color: {styles.DANGER}; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_TIGHT + 2,
            styles.SPACING_WIDE, styles.SPACING_TIGHT + 2,
        )
        layout.setSpacing(styles.SPACING_GRID + 2)

        if rank is not None:
            rank_label = QLabel(f"#{rank}")
            rank_label.setFixedWidth(24)
            rank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rank_label.setStyleSheet(
                f"color: {styles.DANGER};"
                f" font-family: {styles.FONT_MONO};"
                f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
                " letter-spacing: 0.5px;"
            )
            layout.addWidget(rank_label)

        portrait = QLabel()
        portrait.setFixedSize(ICON_SIZE, ICON_SIZE)
        portrait.setScaledContents(True)
        portrait.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border: 1px solid {styles.BORDER_FAINT};"
        )
        if icon is not None and not icon.isNull():
            portrait.setPixmap(icon)
        layout.addWidget(portrait)

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        name = QLabel(suggestion.champion_key)
        name.setStyleSheet(
            f"font-weight: 700; font-size: {styles.FS_BODY}px;"
            f" color: {styles.TEXT_PRIMARY};"
        )
        text.addWidget(name)
        if suggestion.reasons:
            reasons = QLabel(" · ".join(suggestion.reasons))
            reasons.setStyleSheet(
                f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
            )
            reasons.setWordWrap(True)
            text.addWidget(reasons)
        layout.addLayout(text, 1)

        # Plain score, no pill — left-border + rank prefix already
        # carry "this is a high-priority ban" without piling on
        # background+border chrome.
        score = QLabel(f"{suggestion.score:.0f}")
        score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        score.setStyleSheet(
            f"color: {styles.DANGER};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_BODY}px; font-weight: 700;"
            " letter-spacing: 0.4px;"
        )
        score.setFixedWidth(36)
        layout.addWidget(score)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.ban_hover_requested.emit(self._champion_key)
        super().mousePressEvent(event)


class BanPanel(QFrame):
    """Two-column ban-suggestion panel: lane-targeted (left) + allround (right)."""

    ban_hover_requested = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # Two-column header
        header = QHBoxLayout()
        header.setSpacing(styles.SPACING_GRID)
        lane_title = QLabel("Lane Bans")
        lane_title.setObjectName("sectionTitle")
        allround_title = QLabel("Allround Bans")
        allround_title.setObjectName("sectionTitle")
        header.addWidget(lane_title, 1)
        header.addWidget(allround_title, 1)
        outer.addLayout(header)

        # Two-column ban rows
        cols = QHBoxLayout()
        cols.setSpacing(styles.SPACING_GRID)

        self._lane_col = QVBoxLayout()
        self._lane_col.setSpacing(3)
        self._allround_col = QVBoxLayout()
        self._allround_col.setSpacing(3)
        cols.addLayout(self._lane_col, 1)
        cols.addLayout(self._allround_col, 1)
        outer.addLayout(cols)

        self._empty = QLabel("Ban suggestions appear once tier-list data is loaded.")
        self._empty.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            f" padding: 6px 4px; font-style: italic;"
        )
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        self.hide()

    def update_suggestions(
        self,
        suggestions: list[BanSuggestion],
        icon_lookup,  # type: ignore[no-untyped-def]
    ) -> None:
        """Legacy single-list update — routes all to lane column for backward compat."""
        self.update_suggestions_categorized(suggestions, [], icon_lookup)

    def update_suggestions_categorized(
        self,
        lane: list[BanSuggestion],
        allround: list[BanSuggestion],
        icon_lookup,  # type: ignore[no-untyped-def]
    ) -> None:
        """Replace both columns with new lane + allround suggestions."""
        self._clear_col(self._lane_col)
        self._clear_col(self._allround_col)

        if not lane and not allround:
            self._empty.show()
            self.hide()
            return

        self.show()
        self._empty.hide()

        for idx, s in enumerate(lane[:5], start=1):
            row = _BanRow(s, icon_lookup(s.champion_key), rank=idx)
            row.ban_hover_requested.connect(self.ban_hover_requested.emit)
            self._lane_col.addWidget(row)

        for idx, s in enumerate(allround[:5], start=1):
            row = _BanRow(s, icon_lookup(s.champion_key), rank=idx)
            row.ban_hover_requested.connect(self.ban_hover_requested.emit)
            self._allround_col.addWidget(row)

    @staticmethod
    def _clear_col(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
