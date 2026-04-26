"""Shared Qt widgets (status bar, tier badge)."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QStatusBar

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
    """Bottom status bar showing the LCU connection state."""

    def __init__(self) -> None:
        super().__init__()
        self.setSizeGripEnabled(False)
        self._label = QLabel("")
        self._label.setObjectName("connectionStateLabel")
        self.addPermanentWidget(self._label)
        self.set_state("disconnected")

    def set_state(self, state: ConnectionState) -> None:
        self._state: ConnectionState = state
        text = _STATE_LABELS[state]
        color = _STATE_COLORS[state]
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color}; padding: 0 8px;")

    @property
    def state(self) -> ConnectionState:
        return self._state


class TierBadge(QLabel):
    """Small colored label showing a champion tier."""

    def __init__(self, tier: str | None) -> None:
        super().__init__(tier or "—")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        color = styles.TIER_COLORS.get(tier or "", styles.TEXT_MUTED)
        self.setStyleSheet(
            f"color: {color}; font-weight: 700; padding: 2px 6px; "
            f"border: 1px solid {color}; border-radius: 4px;"
        )
        self.setFixedHeight(20)
