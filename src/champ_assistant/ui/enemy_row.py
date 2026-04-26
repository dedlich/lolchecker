"""Enemy team row: enemy champion + their counters."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..data.models import CounterEntry, TeamMember
from . import styles

ICON_SIZE = 32


class EnemyRow(QFrame):
    """One enemy slot showing the locked champion and their counters."""

    PLACEHOLDER = "—"

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("card", True)

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

        self._role_label = QLabel("")
        self._role_label.setProperty("role", "muted")
        self._role_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._role_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")

        head.addWidget(self._icon_label)
        head.addWidget(self._champion_label)
        head.addStretch()
        head.addWidget(self._role_label)

        self._counters_label = QLabel("")
        self._counters_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")
        self._counters_label.setWordWrap(True)

        outer.addLayout(head)
        outer.addWidget(self._counters_label)

        self.clear()

    def clear(self) -> None:
        self._champion_label.setText(self.PLACEHOLDER)
        self._role_label.setText("")
        self._counters_label.setText("")
        self._icon_label.clear()

    def set_data(
        self,
        member: TeamMember,
        champion_name: str | None,
        counters: list[CounterEntry],
        icon: QPixmap | None = None,
    ) -> None:
        if member.champion_id == 0:
            self._champion_label.setText(self.PLACEHOLDER)
            self._icon_label.clear()
        else:
            self._champion_label.setText(champion_name or f"Champion #{member.champion_id}")
            if icon is not None and not icon.isNull():
                self._icon_label.setPixmap(icon)
            else:
                self._icon_label.clear()

        self._role_label.setText(member.assigned_position or "")

        if counters:
            top = counters[:3]
            text = "Counters: " + ", ".join(
                f"{c.champion} ({c.score:.1f})" for c in top
            )
            self._counters_label.setText(text)
        else:
            self._counters_label.setText("")
