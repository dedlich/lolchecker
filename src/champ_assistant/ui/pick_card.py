"""Pick suggestion card.

Visual hierarchy (revised for clarity):

  PRIMARY     champion name + portrait + tier badge — what to read first
  SECONDARY   rank prefix (#1 / #2 / #3) — order signal
  TERTIARY    score number + reason line — supporting context

The previous layout rendered the score at FS_HEADING accent which
visually competed with the champion name. Now the score sits in
TEXT_SECONDARY at FS_BODY, the rank prefix replaces it as the lead
"position" signal.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
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
    """One suggested pick. Layout:

        ┌────────────────────────────────────────┐
        │ #1 [icon]  Name [Tier]    score=84    │  head
        │ reason1 · reason2 · reason3            │  reasons
        │ 🛡 KeystoneA • RuneB • RuneC           │  optional build
        │ ⚔  Item1 ›  Item2 ›  Item3              │
        │ ✨ Flash • Ignite                       │
        │                          [Apply Build] │  optional action
        └────────────────────────────────────────┘
    """

    apply_build_requested = pyqtSignal(str, "PyQt_PyObject", "PyQt_PyObject")
    # (champion_key, rune_names, item_names)

    pick_hover_requested = pyqtSignal(str)
    # (champion_key) — fired when the user clicks anywhere on the card
    # body (NOT the Apply Build button). The handler asks LCU to hover
    # this champion in the player's pick slot. Hover-only — final
    # lock-in stays manual to avoid griefing.

    def __init__(
        self,
        suggestion: PickSuggestion,
        icon: QPixmap | None = None,
        build: ChampionBuild | None = None,
        *,
        rank: int | None = None,
        build_reasons: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.setProperty("card", True)
        self.suggestion = suggestion
        self._build = build
        # Cursor signals "this surface does something on click".
        # The Apply Build QPushButton accepts its own click events
        # so it doesn't propagate up to mousePressEvent below.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_GRID,
            styles.SPACING_WIDE, styles.SPACING_GRID,
        )
        outer.setSpacing(styles.SPACING_TIGHT + 2)

        # -- Head row -----------------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(styles.SPACING_GRID)

        if rank is not None:
            head.addWidget(_rank_badge(rank))

        if icon is not None and not icon.isNull():
            icon_label = QLabel()
            icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
            icon_label.setPixmap(icon)
            icon_label.setStyleSheet(
                f"background-color: {styles.BG_PRIMARY};"
                f" border-radius: {styles.RADIUS}px;"
                f" border: 1px solid {styles.BORDER};"
            )
            head.addWidget(icon_label)

        name = QLabel(suggestion.champion_key)
        name.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        head.addWidget(name)
        head.addWidget(TierBadge(suggestion.tier))
        head.addStretch()

        # Score is now a small muted-secondary label, not a primary
        # accent number. Visual hierarchy: name + tier dominate; score
        # supports without competing.
        score_label = QLabel(f"score {suggestion.score:.0f}")
        score_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            f" font-family: {styles.FONT_MONO};"
            " letter-spacing: 0.4px;"
        )
        score_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        head.addWidget(score_label)
        outer.addLayout(head)

        # -- Reasons line -------------------------------------------------
        reasons_text = " · ".join(suggestion.reasons[:3]) if suggestion.reasons else ""
        reasons = QLabel(reasons_text)
        reasons.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " padding-left: 4px;"
        )
        reasons.setWordWrap(True)
        outer.addWidget(reasons)

        # -- Build section (runes / items / summoners + apply button) ----
        if build is not None:
            self._add_build_lines(outer, build)
            # Matchup-adaptation reasons (e.g. "vs AP-heavy: → Mercury's
            # Treads") show right under the build lines, before the
            # apply button. Subtle accent-color one-liner per reason.
            if build_reasons:
                for reason in build_reasons:
                    label = QLabel(f"⚙ {reason}")
                    label.setStyleSheet(
                        f"color: {styles.ACCENT};"
                        f" font-size: {styles.FS_LABEL}px;"
                        " font-style: italic;"
                        " padding-left: 4px;"
                    )
                    label.setWordWrap(True)
                    outer.addWidget(label)
            self._add_apply_button(outer, build)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """Click on the card body → hover this champion in the picker.
        Clicks on the Apply Build button never reach here (the button
        accepts and consumes its own events)."""
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.pick_hover_requested.emit(self.suggestion.champion_key)
        super().mousePressEvent(event)

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


def _rank_badge(rank: int) -> QLabel:
    """Small badge showing the suggestion's rank (1-indexed). Token-
    driven: accent text on a transparent background, mono font for
    a stable digit width across #1..#9."""
    label = QLabel(f"#{rank}")
    label.setFixedWidth(28)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(
        f"color: {styles.ACCENT};"
        f" font-family: {styles.FONT_MONO};"
        f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
        " letter-spacing: 0.5px;"
    )
    return label


def _build_line(sigil: str, items: list[str], color: str, *, sep: str) -> QLabel:
    label = QLabel(
        f"<span style='color:{color}; font-size:{styles.FS_HEADING}px;'>{sigil}</span>  "
        f"<span style='color:{color}'>"
        f"{sep.join(items)}</span>"
    )
    label.setStyleSheet(f"font-size: {styles.FS_LABEL}px;")
    label.setWordWrap(True)
    return label
