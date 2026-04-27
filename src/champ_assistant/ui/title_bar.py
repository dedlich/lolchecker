"""Slim draggable title bar for the frameless overlay window.

Hosts the app title plus collapse/close buttons. Mouse drag relays to the
parent window via the ``drag_started`` / ``drag_moved`` / ``drag_finished``
signals so the overlay can use the system window manager to move itself
(no manual setGeometry — keeps multi-monitor + DPI scaling correct).
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from . import styles


class TitleBar(QFrame):
    HEIGHT = 28

    minimize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    drag_delta = pyqtSignal(QPoint)
    drag_started = pyqtSignal(QPoint)
    drag_finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("titleBar")
        self.setStyleSheet(
            f"#titleBar {{ background-color: {styles.BG_SECONDARY};"
            f" border-top-left-radius: {styles.RADIUS}px;"
            f" border-top-right-radius: {styles.RADIUS}px;"
            f" border-bottom: 1px solid {styles.BORDER}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(6)

        self._title = QLabel("Champ Assistant")
        self._title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-weight: 700;"
            " font-size: 11px; letter-spacing: 0.3px;"
        )
        layout.addWidget(self._title)

        self._version = QLabel("")
        self._version.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-family: {styles.FONT_MONO};"
            " font-size: 10px; padding-left: 6px;"
        )
        layout.addWidget(self._version, 1)

        self._minimize = self._mk_button("—")  # em-dash as a long minus
        self._minimize.clicked.connect(self.minimize_clicked.emit)
        layout.addWidget(self._minimize)

        self._close = self._mk_button("✕")  # multiplication X
        self._close.setStyleSheet(
            self._close.styleSheet()
            + f" QPushButton:hover {{ background-color: {styles.DANGER}; }}"
        )
        self._close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self._close)

        self._drag_origin: QPoint | None = None

    def _mk_button(self, glyph: str) -> QPushButton:
        b = QPushButton(glyph)
        b.setFixedSize(22, 22)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {styles.TEXT_MUTED};"
            f" border: none; border-radius: {styles.RADIUS_SMALL}px; font-size: 11px; }}"
            f" QPushButton:hover {{ background-color: {styles.BG_ELEVATED};"
            f" color: {styles.TEXT_PRIMARY}; }}"
        )
        return b

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_version(self, version: str) -> None:
        self._version.setText(f"v{version}" if version else "")

    # -- drag handling ----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self.drag_started.emit(self._drag_origin)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is None:
            return
        current = event.globalPosition().toPoint()
        delta = current - self._drag_origin
        self._drag_origin = current
        self.drag_delta.emit(delta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
