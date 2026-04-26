"""Enemy team row: enemy champion + their counters."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..data.models import CounterEntry, TeamMember
from . import styles

ICON_SIZE = 32


class EnemyRow(QFrame):
    """One enemy slot showing the locked champion and their counters.

    The role badge is clickable: clicking cycles the manual role override
    (Auto → TOP → JUNGLE → MID → BOT → SUPPORT → Auto). The actual cycling
    happens in the orchestrator; this widget just emits ``role_clicked``
    with the current cell_id when the user taps the button.
    """

    PLACEHOLDER = "—"

    role_clicked = pyqtSignal(int)  # emits cell_id

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)
        self._cell_id: int = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(8)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(ICON_SIZE, ICON_SIZE)
        self._icon_label.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY}; "
            f"border-radius: {styles.RADIUS}px; border: 1px solid {styles.BORDER};"
        )
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._champion_label = QLabel(self.PLACEHOLDER)
        self._champion_label.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-weight: 600;"
        )

        self._role_button = QPushButton("")
        self._role_button.setFlat(True)
        self._role_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._role_button.setToolTip(
            "Click to override role: Auto → TOP → JUNGLE → MID → BOT → SUPPORT"
        )
        self._role_button.clicked.connect(self._on_role_clicked)
        self._update_role_button_style(role="", overridden=False)

        head.addWidget(self._icon_label)
        head.addWidget(self._champion_label)
        head.addStretch()
        head.addWidget(self._role_button)

        self._counters_label = QLabel("")
        self._counters_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")
        self._counters_label.setWordWrap(True)

        outer.addLayout(head)
        outer.addWidget(self._counters_label)

        self.clear()

    def clear(self) -> None:
        self._cell_id = -1
        self._champion_label.setText(self.PLACEHOLDER)
        self._update_role_button_style(role="", overridden=False)
        self._counters_label.setText("")
        self._icon_label.clear()

    def set_data(
        self,
        member: TeamMember,
        champion_name: str | None,
        counters: list[CounterEntry],
        icon: QPixmap | None = None,
        resolved_role: str = "",
        role_overridden: bool = False,
    ) -> None:
        self._cell_id = member.cell_id
        if member.champion_id == 0:
            self._champion_label.setText(self.PLACEHOLDER)
            self._icon_label.clear()
        else:
            self._champion_label.setText(champion_name or f"Champion #{member.champion_id}")
            if icon is not None and not icon.isNull():
                self._icon_label.setPixmap(icon)
            else:
                self._icon_label.clear()

        self._update_role_button_style(role=resolved_role, overridden=role_overridden)

        if counters:
            top = counters[:3]
            text = "Counters: " + ", ".join(
                f"{c.champion} ({c.score:.1f})" for c in top
            )
            self._counters_label.setText(text)
        else:
            self._counters_label.setText("")

    # -- Role button -----------------------------------------------------

    def _on_role_clicked(self) -> None:
        if self._cell_id >= 0:
            self.role_clicked.emit(self._cell_id)

    def _update_role_button_style(self, *, role: str, overridden: bool) -> None:
        text = role or "Auto"
        # Asterisk prefix marks a manual override so the user can tell at a
        # glance which slots they've adjusted.
        display = f"★ {text}" if overridden else text
        self._role_button.setText(display)
        color = styles.ACCENT if overridden else styles.TEXT_MUTED
        self._role_button.setStyleSheet(
            f"QPushButton {{ color: {color}; "
            f"background: transparent; border: none; padding: 2px 6px; "
            f"font-size: 11px; }}"
            f"QPushButton:hover {{ color: {styles.TEXT_PRIMARY}; }}"
        )
