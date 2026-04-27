"""Champ-select panel showing the top ban suggestions.

Renders one row per suggestion: champion icon + name + score + a short
reasons line. Hides itself when there are no suggestions (e.g. no Riot
API key configured AND no tier-list data — should be rare).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..advisor.ban_suggestions import BanSuggestion
from . import styles

ICON_SIZE = 28


class _BanRow(QFrame):
    def __init__(self, suggestion: BanSuggestion, icon: QPixmap | None) -> None:
        super().__init__()
        self.setProperty("role", "row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 8, 4)
        layout.setSpacing(8)

        portrait = QLabel()
        portrait.setFixedSize(ICON_SIZE, ICON_SIZE)
        portrait.setScaledContents(True)
        portrait.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border: 1px solid {styles.BORDER};"
        )
        if icon is not None and not icon.isNull():
            portrait.setPixmap(icon)
        layout.addWidget(portrait)

        text = QVBoxLayout()
        text.setSpacing(0)
        text.setContentsMargins(0, 0, 0, 0)
        name = QLabel(suggestion.champion_key)
        name.setStyleSheet("font-weight: 600; font-size: 12px;")
        text.addWidget(name)
        if suggestion.reasons:
            reasons = QLabel(" · ".join(suggestion.reasons))
            reasons.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 10px;")
            reasons.setWordWrap(True)
            text.addWidget(reasons)
        layout.addLayout(text, 1)

        score = QLabel(f"{suggestion.score:.0f}")
        score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        score.setStyleSheet(
            f"color: {styles.DANGER}; font-weight: 700; font-size: 14px;"
        )
        layout.addWidget(score)


class BanPanel(QFrame):
    """Renders ban suggestions as a stacked list of rows."""

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

        self._empty = QLabel("(no enemy data yet)")
        self._empty.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
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
        for s in suggestions:
            row = _BanRow(s, icon_lookup(s.champion_key))
            self._rows.addWidget(row)
