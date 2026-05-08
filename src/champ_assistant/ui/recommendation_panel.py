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

import time

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QTimer,
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
TTL_GRACE_S = 5.0             # seconds a card stays after its TTL hits 0


# Per-category glyph + tint. Icon-on-color reads better than plain
# text for at-a-glance category identification.
_CATEGORY_GLYPHS: dict[str, str] = {
    "objective": "◈",   # diamond — drake/baron/herald
    "tempo":     "▶",   # play arrow — push the lead
    "safety":    "✕",   # x — don't fight
    "lane":      "≡",   # bars — laning play
}

# Win-path anchor — coaching-frame label rendered under each rec.
# Maps the ``Recommendation.win_path`` value (set by the win-path
# tagger in evaluate) to the German italic line + color the user sees.
# Empty string entries are intentionally absent — the row hides the
# anchor entirely when the rec carries no win_path tag.
_WIN_PATH_LABELS: dict[str, str] = {
    "primary_path":    "→ Game Plan: weiter auf Win-Path",
    "spike_window":    "→ Power-Spike-Fenster: Druck JETZT",
    "threat_response": "→ Threat Response: gegen Enemy-Threat",
    "avoid_mistake":   "→ Niemals: vermeidet das Lose-Game",
    "closing_window":  "→ Closing Window: Spiel beenden",
}
_WIN_PATH_COLORS: dict[str, str] = {
    "primary_path":    styles.ACCENT,
    "spike_window":    styles.SUCCESS,
    "threat_response": styles.DANGER,
    "avoid_mistake":   styles.WARNING,
    "closing_window":  styles.ACCENT_BRIGHT,
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
        # TTL countdown state — updated by tick_ttl() every second.
        self._meta_prefix: str = ""
        self._ttl_s: float = 0.0
        self._issued_at: float = 0.0
        self._expired_at: float | None = None
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

        # Body is a vertical stack: rec text on top, optional
        # win-path anchor below ("→ Threat Response: gegen Sustain").
        # The anchor is the v1.10.120 coaching coherence layer — every
        # rec links back to the locked WinCondition.
        from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout
        body_col = QVBoxLayout()
        body_col.setContentsMargins(0, 0, 0, 0)
        body_col.setSpacing(2)

        self._text = QLabel("")
        self._text.setWordWrap(True)
        # QLabel + word-wrap inside QHBoxLayout returns single-line
        # sizeHint() which makes the parent QVBoxLayout undersize each
        # row → multi-line messages get clipped at the bottom. Setting
        # MinimumExpanding lets the label tell its parent it wants more
        # vertical space when wrap kicks in. Combined with the row's own
        # vertical Expanding policy it solves the "only top message
        # fully present" symptom from v1.10.83.
        self._text.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )
        # Initial pill stylesheet — render() applies the per-severity
        # color overlay later but PRESERVES the pill background so the
        # message stays readable on top of the in-game scene. Padding
        # keeps the pill snug around the text rather than extending to
        # the row edges.
        self._text.setStyleSheet(self._pill_stylesheet(styles.TEXT_PRIMARY))
        body_col.addWidget(self._text)

        # Win-path anchor — small italic line under the text. Hidden
        # when rec.win_path is empty (untagged rec). Color matches the
        # win_path category so the user can scan severity (text color)
        # AND coaching-frame (anchor color) at a glance.
        self._win_path_anchor = QLabel("")
        self._win_path_anchor.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-style: italic;"
            " padding-left: 2px;"
        )
        self._win_path_anchor.setWordWrap(True)
        self._win_path_anchor.hide()
        body_col.addWidget(self._win_path_anchor)

        layout.addLayout(body_col, 1)
        # Meta label still allocated so existing render() paths don't crash,
        # but it's hidden in chat-mode — no TTL/confidence chrome.
        self._meta = QLabel("")
        self._meta.hide()

    def render(self, rec: Recommendation) -> None:
        # Tint the body text by severity so the message reads "alert" /
        # "warn" / "info" without any panel chrome. KEEP the dark pill
        # background — without it text is unreadable over bright in-game
        # scenes. (v1.10.83 introduced the pill but the prior chat-mode
        # render() was overwriting it; v1.10.84 restores via _pill_stylesheet.)
        color = self._color_for(rec.severity)
        self._text.setText(rec.text)
        self._text.setStyleSheet(self._pill_stylesheet(color))
        self._glyph.setText(_CATEGORY_GLYPHS.get(rec.category, "•"))
        self._glyph.setStyleSheet(self._glyph_stylesheet(rec.severity))
        # Win-path anchor — coaching-frame label below the rec text.
        # Empty win_path → hide (untagged rec, e.g. legacy rule).
        anchor_text = _WIN_PATH_LABELS.get(rec.win_path, "")
        if anchor_text:
            anchor_color = _WIN_PATH_COLORS.get(rec.win_path, styles.TEXT_MUTED)
            self._win_path_anchor.setText(anchor_text)
            self._win_path_anchor.setStyleSheet(
                f"color: {anchor_color};"
                f" font-size: {styles.FS_LABEL}px;"
                " font-style: italic;"
                " padding-left: 2px;"
            )
            self._win_path_anchor.show()
        else:
            self._win_path_anchor.hide()
        # Background is always transparent in chat-mode — no glow variant.
        self.setStyleSheet(self._stylesheet_for(rec.severity))
        # Stash for paintEvent — confidence bar at bottom of the card.
        self._severity = rec.severity
        self._confidence = max(0.0, min(1.0, rec.confidence))
        # Meta-line: "78% • MEDIUM • 12s" (TTL ticks down each second).
        self._meta_prefix = f"{int(rec.confidence * 100)}% • {rec.risk}"
        self._ttl_s = rec.ttl_s or 0.0
        self._issued_at = time.monotonic()
        self._expired_at = None
        self._update_ttl_display(self._ttl_s)
        # Pulse only when this is a high-priority alert AND the
        # engine is confident enough to warrant the attention.
        if rec.severity == "alert" and rec.confidence >= PULSE_PRIORITY_THRESHOLD:
            self._start_pulse()
        else:
            self._stop_pulse()
        self.update()

    def _update_ttl_display(self, remaining: float) -> None:
        meta = self._meta_prefix
        if self._ttl_s > 0:
            meta += f" • {int(remaining)}s"
        self._meta.setText(meta)

    def tick_ttl(self, now: float) -> float | None:
        """Decrement displayed TTL. Returns remaining seconds (≥0) or None when
        no TTL is set. Marks the card as expired when remaining first hits 0."""
        if self._ttl_s <= 0:
            return None
        remaining = max(0.0, self._ttl_s - (now - self._issued_at))
        self._update_ttl_display(remaining)
        if remaining == 0.0 and self._expired_at is None:
            self._expired_at = now
        return remaining

    def is_past_grace(self, now: float) -> bool:
        """True once the card has shown 0s for longer than TTL_GRACE_S."""
        return (
            self._expired_at is not None
            and now - self._expired_at >= TTL_GRACE_S
        )

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
        # Chat-mode: no confidence bar, no background — let the
        # transparent card render exactly nothing.
        super().paintEvent(event)

    @staticmethod
    def _color_for(severity: str) -> str:
        return {
            "alert": styles.DANGER,
            "warn":  styles.WARNING,
            "info":  styles.ACCENT,
        }.get(severity, styles.TEXT_MUTED)

    @classmethod
    def _stylesheet_for(cls, severity: str, *, glow: bool = False) -> str:
        # Chat-style: no card background, no border, no left strip.
        # The body label carries the severity color directly so the
        # message is read as plain text floating over the game.
        return "QFrame[rec-row='true'] { background: transparent; border: none; }"

    @staticmethod
    def _pill_stylesheet(text_color: str) -> str:
        """Translucent dark pill behind the message text. The text color
        rotates per severity (DANGER / WARNING / ACCENT); the pill
        background stays the same so the message reads against any
        in-game scene."""
        return (
            f"QLabel {{ color: {text_color};"
            f" font-size: {styles.FS_BODY}px; font-weight: 700;"
            f" background-color: rgba(0, 0, 0, 180);"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" padding: 4px 8px; }}"
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
    DEFAULT_SIZE = (440, 520)

    def __init__(self) -> None:
        super().__init__()
        # Pure chat overlay — fully transparent panel, no border, no
        # backdrop. FloatingWidget's base class adds a 28-blur drop-shadow
        # on the panel rect for its "lifted card" look; since we have no
        # background, that shadow renders as a phantom rounded outline.
        # Drop the panel-level effect (per-row text gets its own shadow).
        self.setGraphicsEffect(None)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        # Focus mode — collapses to top-1 only when active. Toggled by
        # set_focus_mode(). Default off; the user opts in via Settings.
        self._focus_mode = False
        self._last_recs: list[Recommendation] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_GRID, styles.SPACING_GRID,
            styles.SPACING_GRID, styles.SPACING_GRID,
        )
        # Wider spacing between rows so the per-row dark pills don't
        # visually run into each other. The pill + win_path anchor
        # combo is visually heavier than the old card-with-strip row,
        # so we stack a much taller gap to separate adjacent rows
        # (v1.10.121 user report: "messages 2+ overlap, can't read").
        outer.setSpacing(styles.SPACING_LOOSE * 2)

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

        # 1-second ticker: keeps the TTL counter live between LCDA snapshots
        # and hides cards that have been at 0s for longer than TTL_GRACE_S.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    # -- public API ------------------------------------------------------

    def set_recommendations(self, recs: list[Recommendation]) -> None:
        """Render top-N recommendations (already severity-sorted by
        ``decision_engine.evaluate``). Empty list → hide widget.
        When focus_mode is on, collapses to top-1 only."""
        self._last_recs = list(recs)
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
        """Toggle focus mode at runtime. Re-renders immediately with the
        stored rec list so the change is visible without waiting for the
        next LCDA snapshot."""
        if self._focus_mode == on:
            return
        self._focus_mode = on
        self.set_recommendations(self._last_recs)

    def _on_tick(self) -> None:
        now = time.monotonic()
        any_visible = False
        for row in self._rows:
            if not row.isVisible():
                continue
            remaining = row.tick_ttl(now)
            if remaining is not None and row.is_past_grace(now):
                row.hide()
                row._stop_pulse()
            else:
                any_visible = True
        if not any_visible and self.isVisible():
            self.hide()

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
