"""Pick suggestion card."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..advisor.picks import PickSuggestion
from . import styles
from .widgets import TierBadge

ICON_SIZE = 28


class PickCard(QFrame):
    """Card showing one suggested pick: icon + champion + tier + score + reasons."""

    def __init__(
        self,
        suggestion: PickSuggestion,
        icon: QPixmap | None = None,
    ) -> None:
        super().__init__()
        self.setProperty("card", True)
        self.suggestion = suggestion

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)

        if icon is not None and not icon.isNull():
            icon_label = QLabel()
            icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
            icon_label.setPixmap(icon)
            icon_label.setStyleSheet(
                f"background-color: {styles.BG_PRIMARY}; "
                f"border-radius: {styles.RADIUS}px; "
                f"border: 1px solid {styles.BORDER};"
            )
            head.addWidget(icon_label)

        name = QLabel(suggestion.champion_key)
        name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: 14px; font-weight: 600;"
        )
        head.addWidget(name)
        head.addWidget(TierBadge(suggestion.tier))
        head.addStretch()

        score_label = QLabel(f"{suggestion.score:.0f}")
        score_label.setStyleSheet(
            f"color: {styles.ACCENT}; font-weight: 700; font-size: 14px;"
        )
        score_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        head.addWidget(score_label)

        reasons_text = " · ".join(suggestion.reasons[:3]) if suggestion.reasons else ""
        reasons = QLabel(reasons_text)
        reasons.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
        reasons.setWordWrap(True)

        outer.addLayout(head)
        outer.addWidget(reasons)
