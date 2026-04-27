"""In-game objective timer panel.

Hidden whenever LCDA is unreachable (i.e., not in a match). Once
``update_snapshot`` is called with a real snapshot, the panel becomes
visible and shows next-spawn / kill-by-whom for Dragon, Baron, Herald.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout

from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles


def _fmt_remaining(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "UP"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    return f"{minutes:d}:{sec:02d}"


class ObjectivePanel(QFrame):
    """Compact panel showing the next Dragon/Baron/Herald spawn timers."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)
        self.setObjectName("objectivePanel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        title = QLabel("Live Game — Objectives")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)

        self._game_time_label = QLabel("—")
        self._game_time_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")
        outer.addWidget(self._game_time_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)

        # Three rows: Dragon / Baron / Herald
        self._rows: dict[str, dict[str, QLabel]] = {}
        for r, name in enumerate(("Dragon", "Baron", "Herald")):
            name_label = QLabel(name)
            name_label.setStyleSheet("font-weight: 600;")
            timer_label = QLabel("—")
            timer_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            timer_label.setStyleSheet(f"color: {styles.TIER_A};")
            detail_label = QLabel("")
            detail_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")
            grid.addWidget(name_label, r, 0)
            grid.addWidget(timer_label, r, 1)
            grid.addWidget(detail_label, r, 2)
            self._rows[name] = {
                "name": name_label,
                "timer": timer_label,
                "detail": detail_label,
            }

        outer.addLayout(grid)
        self.hide()  # only visible during a live game

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.show()
        gt = snapshot.game_time
        mm, ss = divmod(int(gt), 60)
        self._game_time_label.setText(f"Game time: {mm:d}:{ss:02d}")

        by_name = {o.name: o for o in snapshot.objectives}
        for name, widgets in self._rows.items():
            obj = by_name.get(name)
            widgets["timer"].setText(self._timer_text(obj, gt))
            widgets["detail"].setText(self._detail_text(obj))

    @staticmethod
    def _timer_text(obj: ObjectiveTimer | None, game_time: float) -> str:
        if obj is None:
            return "—"
        return _fmt_remaining(obj.remaining(game_time))

    @staticmethod
    def _detail_text(obj: ObjectiveTimer | None) -> str:
        if obj is None or obj.last_killed_seconds is None:
            return ""
        bits = []
        if obj.detail:
            bits.append(obj.detail)
        if obj.last_killer:
            bits.append(f"by {obj.last_killer}")
        return " ".join(bits)
