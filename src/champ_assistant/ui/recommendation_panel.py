"""Floating recommendation panel — surfaces decision-engine output.

Charter B1 V2+ — visible UI surface for the engine. Layout pattern
mirrors a modern notification center: each rec sits in a card with a
severity-colored left strip, a category-glyph badge, and the body
text. Top-left by default, draggable like any other FloatingWidget.
Auto-hides when zero recs are active.

Demo mode
=========
``populate_demo()`` fills the panel with one example per rule for
visual validation without needing a live LCDA snapshot. Drives the
``--demo-recommendations`` CLI flag.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
)
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from ..advisor.decision_engine import Recommendation
from . import styles
from .floating_widget import FloatingWidget

MAX_VISIBLE_ROWS = 3
FOCUS_MODE_ROWS = 1
CONFIDENCE_BAR_HEIGHT_PX = 3  # thin strip at the bottom of each rec card
PULSE_DURATION_MS = 1200      # full 1→0.85→1 cycle for high-priority alerts
PULSE_PRIORITY_THRESHOLD = 0.8


# Per-category glyph + tint. Icon-on-color reads better than plain
# text for at-a-glance category identification.
_CATEGORY_GLYPHS: dict[str, str] = {
    "objective": "◈",   # diamond — drake/baron/herald
    "tempo":     "▶",   # play arrow — push the lead
    "safety":    "✕",   # x — don't fight
    "lane":      "≡",   # bars — laning play
}


class _RecRow(QFrame):
    """One recommendation card. Left strip = severity, glyph =
    category, body = text. Re-set via ``render`` so each row is
    pre-allocated; layout shifts never fire on rec churn."""

    STRIP_W = 3

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("rec-row", True)
        self.setStyleSheet(self._stylesheet_for("info"))
        # Confidence-bar state — set by render(). Defaults make the
        # bar invisible until a real Recommendation lands.
        self._severity: str | None = None
        self._confidence: float = 0.0
        # Pulse animation — opacity oscillates 1.0 → 0.85 → 1.0 in a
        # loop on high-priority alerts. Cheap, non-intrusive attention
        # cue. Only set up once; start/stop_pulse() drive the QPropertyAnimation.
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._pulse_anim: QPropertyAnimation | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            styles.SPACING_GRID + self.STRIP_W,
            styles.SPACING_TIGHT + 2,
            styles.SPACING_GRID,
            styles.SPACING_TIGHT + 2,
        )
        layout.setSpacing(styles.SPACING_GRID)

        self._glyph = QLabel("•")
        self._glyph.setFixedWidth(20)
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._glyph.setStyleSheet(self._glyph_stylesheet("info"))
        layout.addWidget(self._glyph)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_BODY}px; font-weight: 600;"
        )
        text_col.addWidget(self._text)
        # Meta-row right under the action: "78% • MEDIUM" — quick at-a-
        # glance confidence + risk read without opening the InsightPanel.
        self._meta = QLabel("")
        self._meta.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_CAPTION}px; font-weight: 600;"
            " letter-spacing: 0.4px;"
        )
        text_col.addWidget(self._meta)
        layout.addLayout(text_col, 1)

    def render(self, rec: Recommendation) -> None:
        self._text.setText(rec.text)
        self._glyph.setText(_CATEGORY_GLYPHS.get(rec.category, "•"))
        self._glyph.setStyleSheet(self._glyph_stylesheet(rec.severity))
        # High-confidence + alert → swap to a glow-bordered stylesheet
        # variant for that "this matters" feel without piling on
        # extra UI chrome.
        glow = rec.confidence >= 0.8 and rec.severity == "alert"
        self.setStyleSheet(self._stylesheet_for(rec.severity, glow=glow))
        # Stash for paintEvent — confidence bar at bottom of the card.
        self._severity = rec.severity
        self._confidence = max(0.0, min(1.0, rec.confidence))
        # Meta-line: "78% • MEDIUM • 12s" (TTL only when present).
        meta = f"{int(rec.confidence * 100)}% • {rec.risk}"
        if rec.ttl_s and rec.ttl_s > 0:
            meta += f" • {int(rec.ttl_s)}s"
        self._meta.setText(meta)
        # Pulse only when this is a high-priority alert AND the
        # engine is confident enough to warrant the attention.
        if rec.severity == "alert" and rec.confidence >= PULSE_PRIORITY_THRESHOLD:
            self._start_pulse()
        else:
            self._stop_pulse()
        self.update()

    def _start_pulse(self) -> None:
        if self._pulse_anim is not None:
            return  # already pulsing
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(PULSE_DURATION_MS)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.85)
        anim.setEndValue(1.0)
        anim.setLoopCount(-1)  # infinite
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.start()
        self._pulse_anim = anim

    def _stop_pulse(self) -> None:
        if self._pulse_anim is None:
            return
        self._pulse_anim.stop()
        self._pulse_anim.deleteLater()
        self._pulse_anim = None
        self._opacity_effect.setOpacity(1.0)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        super().paintEvent(event)
        # Render the confidence bar over the bottom-inside edge of the
        # card. Color matches severity, width fills proportional to
        # the rec's confidence.
        if not getattr(self, "_severity", None):
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor(self._color_for(self._severity))
            color.setAlpha(220)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            inner_w = self.width() - 2 * (self.STRIP_W + 4)
            bar_w = int(inner_w * self._confidence)
            painter.drawRoundedRect(
                self.STRIP_W + 4,
                self.height() - CONFIDENCE_BAR_HEIGHT_PX - 4,
                bar_w,
                CONFIDENCE_BAR_HEIGHT_PX,
                CONFIDENCE_BAR_HEIGHT_PX // 2,
                CONFIDENCE_BAR_HEIGHT_PX // 2,
            )
        finally:
            painter.end()

    @staticmethod
    def _color_for(severity: str) -> str:
        return {
            "alert": styles.DANGER,
            "warn":  styles.WARNING,
            "info":  styles.ACCENT,
        }.get(severity, styles.TEXT_MUTED)

    @classmethod
    def _stylesheet_for(cls, severity: str, *, glow: bool = False) -> str:
        color = cls._color_for(severity)
        # Glow variant — full-width accent border instead of just the
        # left strip, plus a brighter background. Reserved for high-
        # confidence alerts so the user catches the call instantly.
        if glow:
            return (
                f"QFrame[rec-row='true'] {{"
                f" background-color: {styles.BG_ELEVATED};"
                f" border-radius: {styles.RADIUS}px;"
                f" border: 2px solid {color};"
                f" }}"
                f" QFrame[rec-row='true']:hover {{"
                f" background-color: {styles.BG_INTERACT};"
                f" }}"
            )
        return (
            f"QFrame[rec-row='true'] {{"
            f" background-color: {styles.BG_TERTIARY};"
            f" border-radius: {styles.RADIUS}px;"
            f" border-left: {cls.STRIP_W}px solid {color};"
            f" }}"
            f" QFrame[rec-row='true']:hover {{"
            f" background-color: {styles.BG_INTERACT};"
            f" }}"
        )

    @classmethod
    def _glyph_stylesheet(cls, severity: str) -> str:
        return (
            f"color: {cls._color_for(severity)};"
            f" font-size: {styles.FS_HEADING}px;"
            " font-weight: 700;"
        )


class RecommendationPanel(FloatingWidget):
    """See module docstring."""

    KEY = "recommendation_panel"
    DEFAULT_POS = (40, 40)
    DEFAULT_SIZE = (400, 200)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(styles.floating_panel_stylesheet())
        # Focus mode — collapses to top-1 only when active. Toggled by
        # set_focus_mode(). Default off; the user opts in via Settings.
        self._focus_mode = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_GRID,
            styles.SPACING_WIDE, styles.SPACING_GRID,
        )
        outer.setSpacing(styles.SPACING_TIGHT + 2)

        # Header strip — small accent dot + section label.
        header = QHBoxLayout()
        header.setSpacing(styles.SPACING_TIGHT)
        header.setContentsMargins(2, 0, 0, 0)
        dot = QLabel("●")
        dot.setStyleSheet(
            f"color: {styles.ACCENT};"
            f" font-size: {styles.FS_BODY}px;"
        )
        header.addWidget(dot)
        title = QLabel("EMPFEHLUNGEN")
        title.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 1.6px;"
        )
        header.addWidget(title, 1)
        outer.addLayout(header)

        # Pre-allocated row cards. Layout never shifts on rec churn.
        self._rows: list[_RecRow] = []
        for _ in range(MAX_VISIBLE_ROWS):
            row = _RecRow()
            row.hide()
            self._rows.append(row)
            outer.addWidget(row)
        outer.addStretch(1)

        # Hidden until set_recommendations is called with non-empty
        # results — silent default beats stale text on screen.
        self.hide()

    # -- public API ------------------------------------------------------

    def set_recommendations(self, recs: list[Recommendation]) -> None:
        """Render top-N recommendations (already severity-sorted by
        ``decision_engine.evaluate``). Empty list → hide widget.
        When focus_mode is on, collapses to top-1 only."""
        if not recs:
            for row in self._rows:
                row.hide()
                row._stop_pulse()
            self.hide()
            return
        cap = FOCUS_MODE_ROWS if self._focus_mode else MAX_VISIBLE_ROWS
        top = recs[:cap]
        for i, row in enumerate(self._rows):
            if i < len(top):
                row.render(top[i])
                row.show()
            else:
                row.hide()
                row._stop_pulse()
        if not self.isVisible():
            self.fade_appear()

    def set_focus_mode(self, on: bool) -> None:
        """Toggle focus mode at runtime. Re-renders the current top
        recommendation with the new cap so the change is visible
        immediately, not only on next snapshot."""
        if self._focus_mode == on:
            return
        self._focus_mode = on
        # Force a re-render with whatever's currently visible.
        active_recs: list[Recommendation] = []
        for row in self._rows:
            if row.isVisible() and row._severity is not None:
                # We can't reconstruct the full Recommendation from
                # the row state alone; just hide extras when entering
                # focus, no-op when exiting (next snapshot will fan
                # them back out).
                pass
        if on:
            for i, row in enumerate(self._rows):
                if i >= FOCUS_MODE_ROWS:
                    row.hide()
                    row._stop_pulse()

    def populate_demo(self) -> None:
        """Fill with example output of every rule for visual testing
        without a live game. Top-3 by severity render. Each demo
        rec carries a representative confidence value so the new
        bottom confidence-bar reads visually on first paint."""
        demo = [
            # Dragon window — free-take window (5v3)
            Recommendation(
                text="Infernal-Drache — SOUL POINT! in 18s — JETZT forcen — Vision + Group",
                severity="alert", category="objective",
                confidence=0.95, risk="LOW", ttl_s=18.0,
                reasons=(
                    "FREE TAKE — Jinx + Lux tot (2 man up)",
                    "Stacks: Wir 3 — Gegner 1",
                    "Gold-Diff: +4200 | 5v3 alive",
                ),
            ),
            # Baron window — numbers advantage
            Recommendation(
                text="Baron in 25s — JETZT Group + Pit-Control",
                severity="alert", category="objective",
                confidence=0.88, risk="MEDIUM", ttl_s=25.0,
                reasons=(
                    "Baron spawnt in 25s",
                    "Numbers-Vorteil 5v4",
                    "Gold-Diff: +3800 | Level: +1.2",
                ),
            ),
            # Fight recommendation with focus target + AoE warning
            Recommendation(
                text="Fight forcen — 74%. Fokus Jinx. ACHTUNG: Orianna — Ball-Shockwave — NICHT CLUSTERN!",
                severity="alert", category="tempo",
                confidence=0.84, risk="MEDIUM", ttl_s=15.0,
                reasons=(
                    "Fight-Chance: 74% (Score +0.48)",
                    "Numbers: 5v5 alive",
                    "Gold-Diff: +4500",
                    "Fokus: Jinx — 9/1 — extrem fed, primäres Carry",
                    "AoE-Warnung: Orianna — Ball-Shockwave — NICHT CLUSTERN!",
                ),
            ),
            # Baron give-up
            Recommendation(
                text="Baron (30s) abgeben — defensiv warten, Konter suchen",
                severity="warn", category="objective",
                confidence=0.85, risk="HIGH", ttl_s=30.0,
                reasons=(
                    "Baron in 30s",
                    "Gold-Diff: -7200 (deutlich hinten)",
                    "Numbers: 4v5",
                    "Baron-Throw = sofortiges GG",
                ),
            ),
            # Avoid fights
            Recommendation(
                text="MEIDE Fights — 32% Chance. Items + Vision farmen.",
                severity="warn", category="safety",
                confidence=0.78, risk="HIGH", ttl_s=20.0,
                reasons=(
                    "Fight-Chance: 32% (Score -0.42)",
                    "Numbers: 4v5",
                    "Gold-Diff: -5800",
                    "Fokus: Jinx — primäres Carry",
                ),
            ),
            Recommendation(
                text="-6200 Gold — Safe spielen, Wellen abräumen, keine Fights",
                severity="warn", category="safety",
                confidence=0.80, risk="HIGH", ttl_s=30.0,
            ),
            Recommendation(
                text="+4500 Gold — Vision pushen, nächstes Objective vorbereiten",
                severity="info", category="tempo",
                confidence=0.75, risk="LOW", ttl_s=20.0,
            ),
            Recommendation(
                text="Late game — group 5, kein Splitpush ohne TP, jeder Death = 50s+",
                severity="info", category="tempo",
                confidence=0.85, risk="MEDIUM", ttl_s=60.0,
            ),
        ]
        self.set_recommendations(demo)
