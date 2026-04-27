"""System tray icon — gives the user a way back to the main overlay
during a game (when overlay-mode hides the main window) and a clean
way to quit without hunting for the X button on a frameless tool window.

Single click on the tray icon: toggle main overlay visibility.
Right-click: standard menu (Show / Quit).
"""
from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import styles


def _icon() -> QIcon:
    """Build a tiny solid-color square as a placeholder icon — Windows
    accepts QIcon from any QPixmap, no .ico bundling needed."""
    pm = QPixmap(32, 32)
    pm.fill()
    pm.setDevicePixelRatio(1.0)
    # Just render the title's first letter as a simple monogram so users
    # can spot it among other tray icons.
    from PyQt6.QtGui import QColor, QPainter, QPen
    painter = QPainter(pm)
    painter.fillRect(pm.rect(), QColor(styles.ACCENT))
    painter.setPen(QPen(QColor("#FFFFFF")))
    font = painter.font()
    font.setPixelSize(20)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pm.rect(), 0x0084, "C")  # AlignCenter
    painter.end()
    return QIcon(pm)


class TrayController(QObject):
    """Owns the QSystemTrayIcon and routes its events back to the overlay."""

    def __init__(self, overlay) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._overlay = overlay
        self._tray = QSystemTrayIcon(_icon())
        self._tray.setToolTip("Champ Assistant")

        menu = QMenu()
        show_action = QAction("Show / Hide main panel", menu)
        show_action.triggered.connect(self._toggle_overlay)
        menu.addAction(show_action)

        unlock_action = QAction("Unlock widgets (re-enable clicks)", menu)
        unlock_action.triggered.connect(self._unlock_widgets)
        menu.addAction(unlock_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Single click on Windows surfaces as Trigger; on macOS only context.
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_overlay()

    def _toggle_overlay(self) -> None:
        if self._overlay.isVisible():
            self._overlay.hide()
        else:
            # Force champselect mode so the wide layout comes back even
            # if LCDA is still alive (user explicitly asked to see it).
            self._overlay._switch_mode("champselect")
            self._overlay.show()
            self._overlay.raise_()
            self._overlay.activateWindow()

    @staticmethod
    def _unlock_widgets() -> None:
        """Re-enable mouse interaction on all floating widgets after the
        user has put them into right-click pass-through mode."""
        from .floating_widget import FloatingWidget
        FloatingWidget.unlock_all()
