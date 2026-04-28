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

    apply_build_requested = pyqtSignal(str, "PyQt_PyObject", "PyQt_PyObject")
    # (champion_key, rune_names, item_names)

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
        """Three compact lines for runes / items / summoners. Each line
        carries a colored leading sigil + bullet-separated content with
        its own accent so the rows visually parse at a glance."""
        if build.runes:
            outer.addWidget(_build_line(
                "🛡", build.runes, styles.TIER_A, sep=" • ",
            ))
        if build.items:
            outer.addWidget(_build_line(
                "⚔", build.items, styles.TIER_S, sep="  ›  ",
            ))
        if build.summoners:
            outer.addWidget(_build_line(
                "✨", build.summoners, styles.TEXT_SECONDARY, sep=" • ",
            ))

    def _add_apply_button(self, outer: QVBoxLayout, build: ChampionBuild) -> None:
        """Apply Build button — pushes the recommended runes into a new
        rune page AND the items into a custom item set in LeagueClient
        via LCU, activates the rune page. Single click, two LCU writes.
        Skipped if the build has neither runes nor items."""
        if not (build.runes or build.items):
            return
        # Adapt the label so it matches what the click actually does.
        if build.runes and build.items:
            label = "Apply Build"
        elif build.runes:
            label = "Apply Runes"
        else:
            label = "Apply Items"

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addStretch(1)
        apply = QPushButton(label)
        apply.setCursor(Qt.CursorShape.PointingHandCursor)
        apply.setStyleSheet(
            f"QPushButton {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {styles.ACCENT_BRIGHT}, stop:1 {styles.ACCENT});"
            f" color: white;"
            f" border: none; padding: 4px 14px;"
            f" border-radius: 8px; font-weight: 700;"
            f" font-size: {styles.FS_LABEL}px;"
            f" letter-spacing: 0.3px; }}"
            f" QPushButton:hover {{ background: {styles.ACCENT_BRIGHT}; }}"
            f" QPushButton:pressed {{ background: {styles.ACCENT}; }}"
            f" QPushButton:disabled {{ background: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_MUTED}; }}"
        )
        apply.clicked.connect(
            lambda: self.apply_build_requested.emit(
                self.suggestion.champion_key,
                list(build.runes),
                list(build.items),
            )
        )
        row.addWidget(apply)
        outer.addLayout(row)


def _build_line(sigil: str, items: list[str], color: str, *, sep: str) -> QLabel:
    label = QLabel(
        f"<span style='color:{color}; font-size:13px;'>{sigil}</span>  "
        f"<span style='color:{color}'>"
        f"{sep.join(items)}</span>"
    )
    label.setStyleSheet(f"font-size: {styles.FS_LABEL}px;")
    label.setWordWrap(True)
    return label
