"""Slim draggable title bar for the frameless overlay window.

Hosts the app title plus collapse/close buttons. Mouse drag relays to the
parent window via the ``drag_started`` / ``drag_moved`` / ``drag_finished``
signals so the overlay can use the system window manager to move itself
(no manual setGeometry — keeps multi-monitor + DPI scaling correct).
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
)

from . import styles


class TitleBar(QFrame):
    HEIGHT = 28

    minimize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    passthrough_toggled = pyqtSignal(bool)       # True = clicks passthrough
    drag_delta = pyqtSignal(QPoint)
    drag_started = pyqtSignal(QPoint)
    drag_finished = pyqtSignal()
    opacity_changed = pyqtSignal(float)         # 0.0..1.0
    panel_toggled = pyqtSignal(str, bool)        # panel-name, visible

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(self.HEIGHT)
        self.setObjectName("titleBar")
        self.setStyleSheet(
            f"#titleBar {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"  stop:0 {styles.BG_SECONDARY}, stop:1 {styles.BG_PRIMARY});"
            f" border-top-left-radius: {styles.RADIUS}px;"
            f" border-top-right-radius: {styles.RADIUS}px;"
            f" border-bottom: 1px solid {styles.BORDER_FAINT}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)

        self._title = QLabel("Champ Assistant")
        self._title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-weight: 700;"
            f" font-size: {styles.FS_LABEL}px; letter-spacing: 0.4px;"
        )
        layout.addWidget(self._title)

        self._version = QLabel("")
        self._version.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_CAPTION}px; padding-left: 4px;"
        )
        layout.addWidget(self._version, 1)

        # Per-section toggles. Each toggle is a small lettered button that
        # the user can click to hide that panel — useful in-game when you
        # only want to see objectives or only summoner cooldowns.
        self._toggles: dict[str, QToolButton] = {}
        for label, key, tip in (
            ("O", "objectives", "Objectives ein/aus"),
            ("S", "summoners",  "Summoner-Cooldowns ein/aus"),
            ("!", "spikes",     "Power Spikes ein/aus"),
        ):
            btn = self._mk_toggle(label, tip)
            btn.toggled.connect(
                lambda checked, k=key: self.panel_toggled.emit(k, checked)
            )
            self._toggles[key] = btn
            layout.addWidget(btn)

        # Opacity slider — 50%..100%. Compact, 60px wide.
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(50, 100)
        self._opacity_slider.setValue(92)
        self._opacity_slider.setFixedWidth(60)
        self._opacity_slider.setToolTip("Transparenz")
        self._opacity_slider.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 4px; background: {styles.BG_TERTIARY};"
            f" border-radius: 2px; }}"
            f" QSlider::handle:horizontal {{ background: {styles.ACCENT};"
            f" width: 10px; margin: -3px 0; border-radius: 5px; }}"
        )
        self._opacity_slider.valueChanged.connect(
            lambda v: self.opacity_changed.emit(v / 100.0)
        )
        layout.addWidget(self._opacity_slider)

        self._passthrough = QToolButton()
        self._passthrough.setText("🔒")
        self._passthrough.setCheckable(True)
        self._passthrough.setFixedSize(22, 22)
        self._passthrough.setCursor(Qt.CursorShape.PointingHandCursor)
        self._passthrough.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._passthrough.setToolTip(
            "Click-through: Klicks gehen durchs Overlay an League weiter "
            "(Titelleiste bleibt klickbar)."
        )
        self._passthrough.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {styles.TEXT_MUTED};"
            f" border: none; border-radius: {styles.RADIUS_SMALL}px;"
            f" font-size: {styles.FS_LABEL}px; }}"
            f" QToolButton:checked {{ color: {styles.WARNING};"
            f" background-color: {styles.BG_TERTIARY}; }}"
            f" QToolButton:hover:!checked {{ color: {styles.TEXT_PRIMARY}; }}"
        )
        self._passthrough.toggled.connect(self.passthrough_toggled.emit)
        layout.addWidget(self._passthrough)

        self._settings = self._mk_button("⚙")
        self._settings.setToolTip("Einstellungen — API-Keys + Region")
        self._settings.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self._settings)

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
            f" border: none; border-radius: {styles.RADIUS_SMALL}px;"
            f" font-size: {styles.FS_LABEL}px; }}"
            f" QPushButton:hover {{ background-color: {styles.BG_ELEVATED};"
            f" color: {styles.TEXT_PRIMARY}; }}"
        )
        return b

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_version(self, version: str) -> None:
        self._version.setText(f"v{version}" if version else "")

    def set_opacity(self, opacity: float) -> None:
        """Sync the slider to a known opacity (0..1)."""
        self._opacity_slider.blockSignals(True)
        self._opacity_slider.setValue(int(round(opacity * 100)))
        self._opacity_slider.blockSignals(False)

    def set_passthrough(self, on: bool) -> None:
        """Sync the lock-button state from outside without re-emitting."""
        self._passthrough.blockSignals(True)
        self._passthrough.setChecked(on)
        self._passthrough.setText("🔓" if on else "🔒")
        self._passthrough.blockSignals(False)

    def set_panel_visible(self, key: str, visible: bool) -> None:
        btn = self._toggles.get(key)
        if btn is None:
            return
        btn.blockSignals(True)
        btn.setChecked(visible)
        btn.blockSignals(False)

    def _mk_toggle(self, glyph: str, tip: str) -> QToolButton:
        b = QToolButton()
        b.setText(glyph)
        b.setCheckable(True)
        b.setChecked(True)
        b.setFixedSize(20, 20)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setToolTip(tip)
        b.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {styles.TEXT_MUTED};"
            f" border: none; border-radius: {styles.RADIUS_SMALL}px;"
            f" font-size: {styles.FS_LABEL}px;"
            f" font-weight: 700; }}"
            f" QToolButton:checked {{ color: {styles.ACCENT};"
            f" background-color: {styles.BG_TERTIARY}; }}"
            f" QToolButton:hover:!checked {{ color: {styles.TEXT_PRIMARY}; }}"
        )
        return b

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
