"""Floating recommendation panel — surfaces decision-engine output.

Charter B1 V2 — adds a visible UI surface for the decision engine's
recommendations. Sits in the top-left by default, draggable like any
other FloatingWidget. Shows the top 3 recommendations sorted by
severity (alert > warn > info). Hides itself when there are zero
active recommendations to avoid screen spam.

Demo mode
=========
``populate_demo()`` fills the panel with one example per rule for
visual validation without needing a live LCDA snapshot. Drives the
``--demo-recommendations`` CLI flag.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout

from ..advisor.decision_engine import Recommendation
from . import styles
from .floating_widget import FloatingWidget

MAX_VISIBLE_ROWS = 3


class RecommendationPanel(FloatingWidget):
    """See module docstring."""

    KEY = "recommendation_panel"
    DEFAULT_POS = (40, 40)
    DEFAULT_SIZE = (380, 160)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(styles.floating_panel_stylesheet())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_TIGHT,
            styles.SPACING_WIDE, styles.SPACING_TIGHT,
        )
        outer.setSpacing(styles.SPACING_TIGHT)

        title = QLabel("EMPFEHLUNGEN")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 1.4px;"
        )
        outer.addWidget(title)

        # Pre-allocate the rows so layout doesn't shift on
        # set_recommendations — empty rows stay hidden.
        self._rows: list[QLabel] = []
        for _ in range(MAX_VISIBLE_ROWS):
            row = QLabel("")
            row.setWordWrap(True)
            row.setStyleSheet(self._row_stylesheet("info"))
            row.hide()
            self._rows.append(row)
            outer.addWidget(row)
        outer.addStretch(1)

        # Hidden until set_recommendations is called with non-empty
        # results — silent default beats stale text on screen.
        self.hide()

    # -- public API ------------------------------------------------------

    def set_recommendations(self, recs: list[Recommendation]) -> None:
        """Render the top-N recommendations (already severity-sorted
        by ``decision_engine.evaluate``). Empty list → hide widget."""
        if not recs:
            for row in self._rows:
                row.hide()
            self.hide()
            return
        top = recs[:MAX_VISIBLE_ROWS]
        for i, row in enumerate(self._rows):
            if i < len(top):
                rec = top[i]
                row.setText(f"{self._glyph(rec.severity)}  {rec.text}")
                row.setStyleSheet(self._row_stylesheet(rec.severity))
                row.show()
            else:
                row.setText("")
                row.hide()
        if not self.isVisible():
            self.fade_appear()

    def populate_demo(self) -> None:
        """Fill with example output of every rule, for visual testing
        without a live game. Each example mirrors what the matching
        rule would produce in real play."""
        demo = [
            Recommendation(
                text="Drache spawnt in 25s — Vision setzen, Side gruppieren",
                severity="alert", category="objective",
            ),
            Recommendation(
                text="Drache (28s) abgeben — Side-Wellen pushen, "
                     "Gold-Diff aufholen",
                severity="warn", category="objective",
            ),
            Recommendation(
                text="-6200 Gold — Safe spielen, Wellen abräumen, keine Fights",
                severity="warn", category="safety",
            ),
            Recommendation(
                text="Level-Nachteil (-2.0) — XP-Wellen sichern, "
                     "keine Skirmishes",
                severity="warn", category="safety",
            ),
            Recommendation(
                text="+4500 Gold — Vision pushen, Wellen kontrollieren, "
                     "nächstes Objective vorbereiten",
                severity="info", category="tempo",
            ),
        ]
        self.set_recommendations(demo)

    # -- styling ---------------------------------------------------------

    @staticmethod
    def _glyph(severity: str) -> str:
        return {
            "alert": "🔥",
            "warn":  "⚠",
            "info":  "•",
        }.get(severity, "•")

    @staticmethod
    def _row_stylesheet(severity: str) -> str:
        color = {
            "alert": styles.DANGER,
            "warn":  styles.WARNING,
            "info":  styles.ACCENT,
        }.get(severity, styles.TEXT_PRIMARY)
        return (
            f"color: {color};"
            f" font-size: {styles.FS_BODY}px; font-weight: 600;"
            " padding: 2px 0px;"
        )
