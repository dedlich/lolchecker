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

        # Update install button (accent gradient).
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

        # "Later" button — flat / muted next to the install button so it
        # never out-competes Install visually but is always available so
        # the user has a clean dismiss path during gameplay.
        self._snooze_button = QPushButton("Später")
        self._snooze_button.setObjectName("updateSnoozeButton")
        self._snooze_button.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" color: {styles.TEXT_MUTED};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: 6px; padding: 3px 10px;"
            f" font-size: {styles.FS_LABEL}px; }}"
            f" QPushButton:hover {{ background: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_PRIMARY}; }}"
        )
        self._snooze_button.hide()
        self.addPermanentWidget(self._snooze_button)

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

    # -- safe mode --------------------------------------------------------

    def show_safe_mode_banner(self, on_resume: Callable[[], None]) -> None:
        """Surface the Safe Mode notice + a Resume button. Reuses the
        update-button slot since both are mutually exclusive (Safe Mode
        disables update checks entirely)."""
        self.set_info(
            "Safe Mode — previous session ended unexpectedly",
            color=styles.WARNING,
        )
        with contextlib.suppress(TypeError):
            self._update_button.clicked.disconnect()
        self._update_button.clicked.connect(on_resume)
        self._update_button.setText("Resume Normal Mode")
        self._update_button.setEnabled(True)
        self._update_button.show()
        # The "Später" button doesn't apply here — Safe Mode is its own
        # explicit dismiss flow.
        self._snooze_button.hide()

    def dismiss_safe_mode_banner(self) -> None:
        """Hide the Safe Mode banner after Resume is clicked. Note that
        Safe Mode itself only disengages on the NEXT launch — the
        banner just acknowledges the user took the action."""
        self.set_info(
            "Resume normal mode on next start",
            color=styles.SUCCESS,
        )
        self._update_button.hide()

    # -- update flow ------------------------------------------------------

    def show_update_available(
        self,
        tag: str,
        on_click: Callable[[], None],
        on_snooze: Callable[[], None] | None = None,
    ) -> None:
        """Show 'Update X verfügbar' + Install + Later buttons.

        The Later button persists a snooze so the user isn't pestered
        for the same tag for 24h. Omitting ``on_snooze`` hides the
        button (e.g. for in-progress / retry states where snoozing
        wouldn't make sense)."""
        self.set_info(f"Update {tag} verfügbar", color=styles.INFO)
        with contextlib.suppress(TypeError):
            self._update_button.clicked.disconnect()
        self._update_button.clicked.connect(on_click)
        self._update_button.setEnabled(True)
        self._update_button.setText("Jetzt installieren")
        self._update_button.show()
        if on_snooze is not None:
            with contextlib.suppress(TypeError):
                self._snooze_button.clicked.disconnect()
            self._snooze_button.clicked.connect(on_snooze)
            self._snooze_button.show()
        else:
            self._snooze_button.hide()

    def dismiss_update(self) -> None:
        """Hide both update buttons + clear the info slot. Called after
        the user clicks Later or after a successful Install handoff."""
        self._update_button.hide()
        self._snooze_button.hide()
        self.clear_info()

    def set_update_progress(self, message: str) -> None:
        """Surface live progress while the update is being installed."""
        self.set_info(message, color=styles.INFO)
        self._update_button.setEnabled(False)
        self._update_button.setText("Lädt…")
        self._snooze_button.hide()  # snoozing mid-download makes no sense

    def update_failed(self, message: str) -> None:
        self.set_info(message, color=styles.WARNING)
        self._update_button.setEnabled(True)
        self._update_button.setText("Erneut versuchen")

    @property
    def state(self) -> ConnectionState:
        return self._state


# Re-exported for back-compat — the implementation lives in badges.
from .badges import TierBadge  # noqa: E402
