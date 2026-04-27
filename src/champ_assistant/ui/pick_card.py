"""Pick suggestion card."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..advisor.picks import PickSuggestion
from ..data.models import ChampionBuild
from . import styles
from .widgets import TierBadge

ICON_SIZE = 28


class PickCard(QFrame):
    """Card showing one suggested pick: icon + champion + tier + score + reasons + build."""

    apply_runes_requested = pyqtSignal(str, list)  # (champion_key, rune_names)

    def __init__(
        self,
        suggestion: PickSuggestion,
        icon: QPixmap | None = None,
        build: ChampionBuild | None = None,
    ) -> None:
        super().__init__()
        self.setProperty("card", True)
        self.suggestion = suggestion
        self._build = build

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

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

        if build is not None:
            self._add_build_lines(outer, build)
            self._add_apply_button(outer, build)

    @staticmethod
    def _add_build_lines(outer: QVBoxLayout, build: ChampionBuild) -> None:
        """Three compact lines for runes / items / summoners. Empty fields skipped."""
        if build.runes:
            runes_label = QLabel("🛡  " + " · ".join(build.runes))
            runes_label.setStyleSheet(
                f"color: {styles.TIER_A}; font-size: 11px;"
            )
            runes_label.setWordWrap(True)
            outer.addWidget(runes_label)
        if build.items:
            items_label = QLabel("⚔  " + " → ".join(build.items))
            items_label.setStyleSheet(
                f"color: {styles.TIER_S}; font-size: 11px;"
            )
            items_label.setWordWrap(True)
            outer.addWidget(items_label)
        if build.summoners:
            summ_label = QLabel("✨  " + " · ".join(build.summoners))
            summ_label.setStyleSheet(
                f"color: {styles.TEXT_MUTED}; font-size: 11px;"
            )
            outer.addWidget(summ_label)

    def _add_apply_button(self, outer: QVBoxLayout, build: ChampionBuild) -> None:
        """Apply Runes button — creates a Champ-Assistant-named rune page
        in the LeagueClient via LCU and activates it. Only shown if the
        build has runes; the rune-name → perk-id mapping silently skips
        anything we can't resolve."""
        if not build.runes:
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        apply = QPushButton("Apply Runes")
        apply.setCursor(Qt.CursorShape.PointingHandCursor)
        apply.setStyleSheet(
            f"QPushButton {{ background-color: {styles.ACCENT_DIM};"
            f" color: {styles.TEXT_PRIMARY};"
            f" border: 1px solid {styles.ACCENT}; padding: 3px 10px;"
            f" border-radius: {styles.RADIUS_SMALL}px; font-weight: 600;"
            f" font-size: 11px; }}"
            f" QPushButton:hover {{ background-color: {styles.ACCENT}; }}"
            f" QPushButton:disabled {{ background-color: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_MUTED}; border-color: {styles.BORDER}; }}"
        )
        apply.clicked.connect(
            lambda: self.apply_runes_requested.emit(
                self.suggestion.champion_key, list(build.runes),
            )
        )
        row.addWidget(apply)
        outer.addLayout(row)
