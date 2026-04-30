"""Recommendation detail view (v2 spec — InsightPanel).

Modal-style centered widget that expands the top recommendation into
a full breakdown — title, confidence + risk badges, bulletpoint
reasons, close button. Triggered by Ctrl+Alt+I (or programmatically).

Hidden by default. Shows the most-recent Recommendation set on the
panel via ``set_recommendation``. Auto-hides on Esc + on the close
button. Doesn't take focus from League — uses ``WA_ShowWithoutActivating``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..advisor.decision_engine import Recommendation
from . import styles

INSIGHT_W = 460
INSIGHT_H = 340


class InsightPanel(QWidget):
    """Modal-ish detail card for the active recommendation."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(INSIGHT_W, INSIGHT_H)

        # Main card frame — opaque interior, the parent widget keeps
        # WA_TranslucentBackground so the rounded corners render
        # cleanly without a square fill bleeding past them.
        self._card = QFrame(self)
        self._card.setObjectName("insightCard")
        self._card.setStyleSheet(
            f"#insightCard {{"
            f" background-color: {styles.BG_SECONDARY};"
            f" border-radius: {styles.RADIUS_LARGE}px;"
            f" border: 1px solid {styles.BORDER_ACCENT};"
            f" }}"
        )
        self._card.setGeometry(0, 0, INSIGHT_W, INSIGHT_H)

        outer = QVBoxLayout(self._card)
        outer.setContentsMargins(
            styles.SPACING_LOOSE, styles.SPACING_LOOSE,
            styles.SPACING_LOOSE, styles.SPACING_LOOSE,
        )
        outer.setSpacing(styles.SPACING_GRID + 2)

        # Title.
        self._title = QLabel("Keine aktive Empfehlung")
        self._title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_TITLE}px; font-weight: 700;"
            " letter-spacing: 0.2px;"
        )
        self._title.setWordWrap(True)
        outer.addWidget(self._title)

        # Meta-row: confidence + risk pills + category.
        meta = QHBoxLayout()
        meta.setSpacing(styles.SPACING_GRID)

        self._confidence = QLabel("Confidence: —")
        self._confidence.setStyleSheet(self._pill_stylesheet(styles.ACCENT))
        meta.addWidget(self._confidence)

        self._risk = QLabel("Risk: —")
        self._risk.setStyleSheet(self._pill_stylesheet(styles.WARNING))
        meta.addWidget(self._risk)

        self._category = QLabel("category")
        self._category.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px; letter-spacing: 0.6px;"
            " padding-left: 8px;"
        )
        meta.addWidget(self._category, 1)
        outer.addLayout(meta)

        # Divider.
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {styles.BORDER_FAINT};")
        outer.addWidget(divider)

        # Reasons section.
        reasons_title = QLabel("● BEGRÜNDUNG")
        reasons_title.setStyleSheet(
            f"color: {styles.ACCENT};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 1.6px;"
        )
        outer.addWidget(reasons_title)

        self._reasons_container = QVBoxLayout()
        self._reasons_container.setSpacing(styles.SPACING_TIGHT)
        self._reasons_container.setContentsMargins(8, 0, 0, 0)
        outer.addLayout(self._reasons_container, 1)

        # Footer — close button.
        footer = QHBoxLayout()
        footer.addStretch(1)
        close_btn = QPushButton("Schließen  (Esc)")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(self._close_button_stylesheet())
        close_btn.clicked.connect(self._on_close)
        footer.addWidget(close_btn)
        outer.addLayout(footer)

        # Esc to dismiss.
        self._esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self._esc_shortcut.activated.connect(self._on_close)

        self.hide()

    # -- public API ------------------------------------------------------

    def set_recommendation(self, rec: Recommendation | None) -> None:
        """Render ``rec`` as the focal detail. None → empty state."""
        if rec is None:
            self._title.setText("Keine aktive Empfehlung")
            self._confidence.setText("Confidence: —")
            self._risk.setText("Risk: —")
            self._category.setText("")
            self._render_reasons(())
            return
        self._title.setText(rec.text)
        self._confidence.setText(f"Confidence: {int(rec.confidence * 100)}%")
        self._confidence.setStyleSheet(
            self._pill_stylesheet(self._confidence_color(rec.confidence))
        )
        self._risk.setText(f"Risk: {rec.risk}")
        self._risk.setStyleSheet(
            self._pill_stylesheet(self._risk_color(rec.risk))
        )
        self._category.setText(rec.category.upper())
        self._render_reasons(rec.reasons)

    def toggle(self, current_top: Recommendation | None) -> None:
        """Show + populate, or hide if already visible."""
        if self.isVisible():
            self.hide()
            self.closed.emit()
            return
        self.set_recommendation(current_top)
        # Re-center each time it opens so monitor changes don't strand it.
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - INSIGHT_W // 2,
                geo.center().y() - INSIGHT_H // 2,
            )
        self.show()

    # -- internals -------------------------------------------------------

    def _render_reasons(self, reasons: tuple[str, ...] | list[str]) -> None:
        """Tear down + rebuild the bulletpoint list."""
        while self._reasons_container.count():
            item = self._reasons_container.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        if not reasons:
            empty = QLabel("Keine ausführliche Begründung verfügbar.")
            empty.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_LABEL}px; font-style: italic;"
            )
            self._reasons_container.addWidget(empty)
            return
        for reason in reasons:
            row = QLabel(f"•  {reason}")
            row.setWordWrap(True)
            row.setStyleSheet(
                f"color: {styles.TEXT_SECONDARY};"
                f" font-size: {styles.FS_BODY}px;"
                " padding: 2px 0;"
            )
            self._reasons_container.addWidget(row)

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    @staticmethod
    def _pill_stylesheet(color: str) -> str:
        return (
            f"color: white;"
            f" background-color: {color};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            f" padding: 3px 10px;"
            f" border-radius: {styles.RADIUS_PILL}px;"
            " letter-spacing: 0.4px;"
        )

    @staticmethod
    def _confidence_color(confidence: float) -> str:
        if confidence >= 0.8:
            return styles.SUCCESS
        if confidence >= 0.5:
            return styles.ACCENT
        return styles.TEXT_MUTED

    @staticmethod
    def _risk_color(risk: str) -> str:
        return {
            "LOW": styles.SUCCESS,
            "MEDIUM": styles.WARNING,
            "HIGH": styles.DANGER,
        }.get(risk, styles.TEXT_MUTED)

    @staticmethod
    def _close_button_stylesheet() -> str:
        return (
            f"QPushButton {{"
            f" background-color: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_PRIMARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS}px;"
            f" padding: 6px 16px;"
            f" font-size: {styles.FS_LABEL}px; font-weight: 600;"
            f" }}"
            f" QPushButton:hover {{"
            f" background-color: {styles.BG_INTERACT};"
            f" border-color: {styles.BORDER_ACCENT};"
            f" }}"
        )
