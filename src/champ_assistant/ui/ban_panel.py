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
        # Subtle red left-border carries the ban semantic.
        self.setStyleSheet(
            f"QFrame[role='row'] {{ background-color: {styles.BG_TERTIARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border-left: 3px solid {styles.DANGER}; }}"
            f" QFrame[role='row']:hover {{ background-color: {styles.BG_INTERACT}; }}"
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
    """Renders ban suggestions as a stacked list of rows."""

    ban_hover_requested = pyqtSignal(str)
    # Bubbled from each _BanRow — the overlay forwards this further
    # up to the orchestrator. One signal per click; the BanPanel
    # owns its rows so we can rewire on every update_suggestions().

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(4)

        title = QLabel("Ban Suggestions")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)

        self._rows = QVBoxLayout()
        self._rows.setSpacing(4)
        outer.addLayout(self._rows)

        self._empty = QLabel(
            "Bans appear when the tier list + enemy profiles disagree on what to fear."
        )
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
        """Replace the list of rendered rows with the new ranking.
        ``icon_lookup`` maps champion-key -> QPixmap (or None)."""
        # Clear old rows
        while self._rows.count():
            item = self._rows.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if not suggestions:
            self.hide()
            return

        self.show()
        self._empty.hide()
        for idx, s in enumerate(suggestions, start=1):
            row = _BanRow(s, icon_lookup(s.champion_key), rank=idx)
            row.ban_hover_requested.connect(self.ban_hover_requested.emit)
            self._rows.addWidget(row)
