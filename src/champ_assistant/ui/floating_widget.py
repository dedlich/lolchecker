"""Base class for independently positioned mini-overlay widgets.

Each FloatingWidget is its own top-level frameless transparent always-on-top
window so users can drag them around freely (Blitz-style modular overlays).
Position persists across sessions, keyed on a per-widget identifier.

Subclasses just override ``KEY`` + ``DEFAULT_POS``/``DEFAULT_SIZE`` and
build their internal layout in ``__init__``.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QFrame

from .. import overlay_config


class FloatingWidget(QFrame):
    KEY: str = "floating"           # subclass overrides — used as persistence key
    DEFAULT_POS: tuple[int, int] = (100, 100)
    DEFAULT_SIZE: tuple[int, int] = (220, 56)

    def __init__(self) -> None:
        super().__init__(parent=None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Background opacity is controlled per-widget via stylesheet rgba —
        # we don't WA_TranslucentBackground so child widgets render normally.
        self.setProperty("panel", True)

        self._drag_origin: QPoint | None = None
        self._load_position()

    # -- drag handling ----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is not None:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self._save_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # -- persistence ------------------------------------------------------

    def _load_position(self) -> None:
        state = overlay_config.load()
        positions = state.floating_positions or {}
        pos = positions.get(self.KEY) or list(self.DEFAULT_POS)
        x, y = int(pos[0]), int(pos[1])
        x, y = self._clamp_to_screen(x, y)
        w, h = self.DEFAULT_SIZE
        self.setGeometry(x, y, w, h)

    def _save_position(self) -> None:
        state = overlay_config.load()
        if state.floating_positions is None:
            state.floating_positions = {}
        state.floating_positions[self.KEY] = [self.x(), self.y()]
        overlay_config.save(state)

    @staticmethod
    def _clamp_to_screen(x: int, y: int) -> tuple[int, int]:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        geo = screen.availableGeometry()
        x = max(geo.left(), min(geo.right() - 80, x))
        y = max(geo.top(), min(geo.bottom() - 40, y))
        return x, y
