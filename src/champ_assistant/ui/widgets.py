"""Shared Qt widgets (status bar, tier badge)."""
from __future__ import annotations

import contextlib
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QStatusBar

from . import styles
from .view_model import ConnectionState

_STATE_LABELS: dict[ConnectionState, str] = {
    "disconnected": "Disconnected",
    "waiting": "Waiting for League Client…",
    "connected": "Connected",
    "reconnecting": "Reconnecting…",
}

_STATE_COLORS: dict[ConnectionState, str] = {
    "disconnected": styles.TEXT_MUTED,
    "waiting": styles.TEXT_MUTED,
    "connected": styles.TIER_A,
    "reconnecting": styles.TIER_S,
}


class ConnectionStatusBar(QStatusBar):
    """Bottom status bar with three independent slots:
      _info_label    — left, persistent (update available, crash error, etc.)
      _state_label   — right, connection state (overwritten on every refresh)

    Without this split, the update notifier and crash subscriber both
    wrote into the same widget the orchestrator overwrites on every
    session refresh, so the user never saw the message.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setSizeGripEnabled(False)

        self._version_label = QLabel("")
        self._version_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_CAPTION}px; padding: 0 8px;"
        )
        self.addWidget(self._version_label)

        self._info_label = QLabel("")
        self._info_label.setObjectName("statusInfoLabel")
        self.addWidget(self._info_label, 1)

        self._update_button = QPushButton("Jetzt installieren")
        self._update_button.setObjectName("updateInstallButton")
        self._update_button.setStyleSheet(
            f"QPushButton {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {styles.ACCENT_BRIGHT}, stop:1 {styles.ACCENT});"
            f" color: white; padding: 3px 14px;"
            f" border-radius: 6px; border: none; font-weight: 700;"
            f" font-size: {styles.FS_LABEL}px; }}"
            f" QPushButton:hover {{ background: {styles.ACCENT_BRIGHT}; }}"
            f" QPushButton:pressed {{ background: {styles.ACCENT}; }}"
            f" QPushButton:disabled {{ background: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_MUTED}; }}"
        )
        self._update_button.hide()
        self.addPermanentWidget(self._update_button)

        # New: connection-state dot to the right of the text label.
        from .badges import StateDot
        self._dot = StateDot("disconnected")
        self.addPermanentWidget(self._dot)

        self._label = QLabel("")  # kept as `_label` for backwards-compat
        self._label.setObjectName("connectionStateLabel")
        self.addPermanentWidget(self._label)

        self.set_state("disconnected")

    def set_state(self, state: ConnectionState) -> None:
        self._state: ConnectionState = state
        text = _STATE_LABELS[state]
        color = _STATE_COLORS[state]
        self._label.setText(text)
        self._label.setStyleSheet(
            f"color: {color}; padding: 0 8px;"
            f" font-size: {styles.FS_LABEL}px; font-weight: 600;"
        )
        self._dot.set_state(state)

    def set_info(self, text: str, color: str | None = None) -> None:
        """Persistent message in the left slot — survives state refreshes."""
        self._info_label.setText(text)
        c = color or _STATE_COLORS["connected"]
        self._info_label.setStyleSheet(f"color: {c}; padding: 0 8px;")

    def clear_info(self) -> None:
        self._info_label.setText("")
        self._info_label.setStyleSheet("")

    def show_update_available(self, tag: str, on_click: Callable[[], None]) -> None:
        """Show 'Update X verfügbar' + an Install button that calls ``on_click``."""
        self.set_info(f"Update {tag} verfügbar", color=styles.INFO)
        with contextlib.suppress(TypeError):
            self._update_button.clicked.disconnect()
        self._update_button.clicked.connect(on_click)
        self._update_button.setEnabled(True)
        self._update_button.setText("Jetzt installieren")
        self._update_button.show()

    def set_update_progress(self, message: str) -> None:
        """Surface live progress while the update is being installed."""
        self.set_info(message, color=styles.INFO)
        self._update_button.setEnabled(False)
        self._update_button.setText("Lädt…")

    def update_failed(self, message: str) -> None:
        self.set_info(message, color=styles.WARNING)
        self._update_button.setEnabled(True)
        self._update_button.setText("Erneut versuchen")

    @property
    def state(self) -> ConnectionState:
        return self._state


# Re-exported for back-compat — the implementation lives in badges.
from .badges import TierBadge  # noqa: E402
