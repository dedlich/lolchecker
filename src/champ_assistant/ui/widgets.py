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

        self._info_label = QLabel("")
        self._info_label.setObjectName("statusInfoLabel")
        self.addWidget(self._info_label, 1)

        self._label = QLabel("")  # kept as `_label` for backwards-compat
        self._label.setObjectName("connectionStateLabel")
        self.addPermanentWidget(self._label)

        self.set_state("disconnected")

    def set_state(self, state: ConnectionState) -> None:
        self._state: ConnectionState = state
        text = _STATE_LABELS[state]
        color = _STATE_COLORS[state]
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color}; padding: 0 8px;")

    def set_info(self, text: str, color: str | None = None) -> None:
        """Persistent message in the left slot — survives state refreshes."""
        self._info_label.setText(text)
        c = color or _STATE_COLORS["connected"]
        self._info_label.setStyleSheet(f"color: {c}; padding: 0 8px;")

    def clear_info(self) -> None:
        self._info_label.setText("")
        self._info_label.setStyleSheet("")

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
