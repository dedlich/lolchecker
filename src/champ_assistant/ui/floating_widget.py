"""Base class for independently positioned mini-overlay widgets.

Each FloatingWidget is its own top-level frameless transparent always-on-top
window so users can drag them around freely (Blitz-style modular overlays).
Position persists across sessions, keyed on a per-widget identifier.

Mouse model:
  - Left-drag → move the widget. Position auto-saves on release.
  - Right-click → toggle pass-through. In pass-through mode the widget
    becomes invisible to mouse input so League gets the clicks. The
    user can re-enable interactivity from the tray menu's "Unlock
    widgets" action (since right-click no longer reaches the widget).

Subclasses just override ``KEY`` + ``DEFAULT_POS``/``DEFAULT_SIZE`` and
build their internal layout in ``__init__``.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QMouseEvent
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect

from .. import layout as layout_module


class FloatingWidget(QFrame):
    KEY: str = "floating"           # subclass overrides — used as persistence key
    DEFAULT_POS: tuple[int, int] = (100, 100)
    DEFAULT_SIZE: tuple[int, int] = (220, 56)

    # Registry so the tray controller can find every live widget and
    # toggle their pass-through state.
    _instances: list["FloatingWidget"] = []

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

        # Subtle drop shadow so the widget visually lifts off the game
        # underneath. Cheap to render and matches the modern overlay feel.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)

        self._drag_origin: QPoint | None = None
        self._load_position()
        FloatingWidget._instances.append(self)
        # Track whether the widget was shown at least once this session so
        # the fade-in animation only fires on the first reveal — not on
        # every snapshot tick.
        self._has_appeared = False

    def __del__(self) -> None:  # noqa: D401
        try:
            FloatingWidget._instances.remove(self)
        except ValueError:
            pass

    # -- show/hide with fade ---------------------------------------------

    def fade_appear(self) -> None:
        """Show the widget with a subtle 180 ms fade. Only animates on
        the first appearance of a session — subsequent updates are
        instant so the timer numbers don't visibly settle."""
        if self._has_appeared and self.isVisible():
            return
        self._has_appeared = True
        # Drop-shadow conflicts with QGraphicsOpacityEffect (only one
        # graphics effect per widget). For the fade we temporarily swap
        # the shadow out, then restore it after the animation finishes.
        from . import styles
        from .anim import fade_in
        anim = fade_in(self, duration_ms=styles.ANIM_DEFAULT_MS)
        anim.finished.connect(self._restore_shadow)

    def _restore_shadow(self) -> None:
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        from . import styles
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(styles.SHADOW_FLOAT["blur"])
        shadow.setOffset(styles.SHADOW_FLOAT["x"], styles.SHADOW_FLOAT["y"])
        shadow.setColor(QColor(0, 0, 0, styles.SHADOW_FLOAT["alpha"]))
        self.setGraphicsEffect(shadow)

    # -- pass-through toggle (called via tray + right-click) -------------

    def set_passthrough(self, on: bool) -> None:
        """When True: every mouse event bypasses this widget so League
        gets the click. When False: drag/right-click work normally."""
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)

    @classmethod
    def unlock_all(cls) -> None:
        """Tray-menu helper: re-enable interaction on every floating widget."""
        for w in cls._instances:
            w.set_passthrough(False)

    # -- drag handling ----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.set_passthrough(True)
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
        """Read the saved layout for this widget's KEY and apply it.
        ``layout.safe_position_for`` handles missing-monitor and
        out-of-bounds clamping so this method never produces an
        off-screen widget."""
        saved = layout_module.store().get(self.KEY)
        x, y = layout_module.safe_position_for(
            saved,
            fallback_pos=self.DEFAULT_POS,
            fallback_size=self.DEFAULT_SIZE,
            widget_key=self.KEY,
        )
        w, h = self.DEFAULT_SIZE
        self.setGeometry(x, y, w, h)
        if saved is not None and not saved.visible:
            self.hide()

    def _save_position(self) -> None:
        """Mark the current position as dirty in the store. The store
        debounces the actual disk write to 500 ms after the last move."""
        layout_module.store().mark(
            self.KEY,
            layout_module.WidgetLayout(
                x=self.x(),
                y=self.y(),
                visible=self.isVisible(),
                monitor_id=layout_module.current_monitor_id(self),
            ),
        )
