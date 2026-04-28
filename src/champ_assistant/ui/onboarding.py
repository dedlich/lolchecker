"""First-launch onboarding banner.

Non-modal, sits inside the main overlay during the user's very first
session. Two responsibilities:
  1. Tell the user in 2-3 sentences what the app does.
  2. Surface the three global hotkeys + the drag affordance so the user
     doesn't need to discover them.

Dismissal flips ``overlay_config.onboarding_seen`` and saves the config
on the same call. Re-launches never re-show. Spec: never blocks the UI,
must be skippable in <10s.
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from . import styles


class OnboardingBanner(QFrame):
    """In-overlay welcome card. Created hidden; ``maybe_show`` decides
    based on persisted state whether to surface it."""

    def __init__(
        self,
        on_dismissed: Callable[[], None],
        parent=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self._on_dismissed = on_dismissed
        self.setObjectName("onboardingBanner")
        # Translucent panel that visually sits on top of the main column —
        # accent border so it reads as "look at me first" without being
        # visually heavy.
        self.setStyleSheet(
            f"#onboardingBanner {{"
            f" background-color: rgba(91, 168, 255, 28);"
            f" border: 1px solid {styles.BORDER_ACCENT};"
            f" border-radius: {styles.RADIUS}px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        title = QLabel("Willkommen bei Champ Assistant")
        title.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
            " letter-spacing: -0.2px;"
        )
        outer.addWidget(title)

        intro = QLabel(
            "Live Counter-Picks und Build-Vorschläge im Champ Select. "
            "Im Spiel zeigen schwebende Mini-Widgets Drachen-/Baron-Timer "
            "und Jungle-Camp-Vorhersagen über deiner Minimap."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY};"
            f" font-size: {styles.FS_BODY}px;"
        )
        outer.addWidget(intro)

        # Hotkey legend — three concise rows, monospace combo + readable label.
        for combo, what in (
            ("Ctrl+Alt+H", "Overlay an/aus schalten"),
            ("Ctrl+Alt+L", "Click-Through (Mausklicks gehen ans Spiel)"),
            ("Ctrl+Alt+R", "Widget-Layout zurücksetzen"),
        ):
            outer.addLayout(_hotkey_row(combo, what))

        # Drag tip — last so the user sees the hotkeys first.
        drag_tip = QLabel(
            "🎯 Mini-Widgets per Linksklick verschieben · "
            "Rechtsklick aktiviert Click-Through einzeln."
        )
        drag_tip.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px;"
            " padding-top: 4px;"
        )
        drag_tip.setWordWrap(True)
        outer.addWidget(drag_tip)

        # Buttons row — Got it (accent) on the right, Skip (flat) muted.
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)

        skip = QPushButton("Überspringen")
        skip.setCursor(Qt.CursorShape.PointingHandCursor)
        skip.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" color: {styles.TEXT_MUTED};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: 6px; padding: 5px 14px;"
            f" font-size: {styles.FS_LABEL}px; }}"
            f" QPushButton:hover {{ background: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_PRIMARY}; }}"
        )
        skip.clicked.connect(self._dismiss)
        button_row.addWidget(skip)

        got_it = QPushButton("Verstanden")
        got_it.setCursor(Qt.CursorShape.PointingHandCursor)
        got_it.setStyleSheet(
            f"QPushButton {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {styles.ACCENT_BRIGHT}, stop:1 {styles.ACCENT});"
            f" color: white; border: none;"
            f" border-radius: 6px; padding: 5px 16px;"
            f" font-weight: 700; font-size: {styles.FS_LABEL}px; }}"
            f" QPushButton:hover {{ background: {styles.ACCENT_BRIGHT}; }}"
        )
        got_it.clicked.connect(self._dismiss)
        button_row.addWidget(got_it)

        outer.addLayout(button_row)

        self.hide()

    def maybe_show(self, already_seen: bool) -> None:
        """Show the banner only if the user hasn't dismissed it before."""
        if already_seen:
            return
        self.show()

    def _dismiss(self) -> None:
        self.hide()
        try:
            self._on_dismissed()
        except Exception:  # noqa: BLE001 — never let a save error break dismiss
            import logging
            logging.getLogger(__name__).exception("onboarding dismiss callback failed")


def _hotkey_row(combo: str, description: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(10)
    combo_label = QLabel(combo)
    combo_label.setFixedWidth(96)
    combo_label.setStyleSheet(
        f"color: {styles.ACCENT};"
        f" font-family: {styles.FONT_MONO};"
        f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
    )
    desc_label = QLabel(description)
    desc_label.setStyleSheet(
        f"color: {styles.TEXT_SECONDARY};"
        f" font-size: {styles.FS_LABEL}px;"
    )
    row.addWidget(combo_label)
    row.addWidget(desc_label, 1)
    return row
