"""In-game objective timer panel.

Hidden whenever LCDA is unreachable (i.e., not in a match). Once
``update_snapshot`` is called with a real snapshot, the panel becomes
visible and shows next-spawn / kill-by-whom for Dragon, Baron, Herald.
Timer color reflects remaining time so the eye locks onto the urgent ones.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles

# A short emoji-style sigil per objective. Pure unicode so we never miss a
# glyph at runtime; replaced with proper SVG icons in a later iteration.
OBJECTIVE_SIGILS = {
    "Dragon": "🐉",
    "Baron": "👑",
    "Herald": "👁",
}


def _fmt_remaining(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "UP"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    return f"{minutes:d}:{sec:02d}"


def _fmt_game_time(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes:d}:{sec:02d}"


class _ObjectiveRow(QFrame):
    """One row inside the objective panel."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setProperty("role", "row")
        self.name = name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 10, 6)
        layout.setSpacing(8)

        self._sigil = QLabel(OBJECTIVE_SIGILS.get(name, "•"))
        self._sigil.setStyleSheet("font-size: 16px;")
        self._sigil.setFixedWidth(24)
        self._sigil.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._sigil)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        text_col.setContentsMargins(0, 0, 0, 0)
        self._name_label = QLabel(name)
        self._name_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self._detail_label = QLabel("")
        self._detail_label.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 10px;")
        text_col.addWidget(self._name_label)
        text_col.addWidget(self._detail_label)
        layout.addLayout(text_col, 1)

        self._timer_label = QLabel("—")
        self._timer_label.setProperty("role", "timer")
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._timer_label.setStyleSheet(f"color: {styles.TEXT_PRIMARY};")
        layout.addWidget(self._timer_label)

    def update_from(self, obj: ObjectiveTimer | None, game_time: float) -> None:
        if obj is None:
            self._timer_label.setText("—")
            self._detail_label.setText("")
            return
        rem = obj.remaining(game_time)
        self._timer_label.setText(_fmt_remaining(rem))
        self._timer_label.setStyleSheet(f"color: {self._timer_color(obj, rem)};")
        self._detail_label.setText(self._detail(obj))

    @staticmethod
    def _timer_color(obj: ObjectiveTimer, rem: float | None) -> str:
        if rem is None:
            return styles.TEXT_DISABLED
        if rem <= 0:
            return styles.SUCCESS  # UP — green
        if rem <= 30:
            return styles.WARNING
        if rem <= 60:
            return styles.ACCENT
        return styles.TEXT_PRIMARY

    @staticmethod
    def _detail(obj: ObjectiveTimer) -> str:
        if obj.last_killed_seconds is None:
            return "noch nicht gefallen"
        bits = []
        if obj.detail:
            bits.append(obj.detail)
        if obj.last_killer:
            bits.append(f"by {obj.last_killer}")
        return " · ".join(bits) or "—"


class ObjectivePanel(QFrame):
    """Compact panel showing the next Dragon/Baron/Herald spawn timers."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("panel", True)
        self.setObjectName("objectivePanel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Live Game — Objectives")
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        self._game_time_label = QLabel("—")
        self._game_time_label.setStyleSheet(
            f"color: {styles.ACCENT}; font-family: {styles.FONT_MONO};"
            " font-size: 11px; font-weight: 700;"
        )
        header.addWidget(self._game_time_label, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(header)

        self._rows: dict[str, _ObjectiveRow] = {}
        for name in ("Dragon", "Baron", "Herald"):
            row = _ObjectiveRow(name)
            outer.addWidget(row)
            self._rows[name] = row

        self.hide()

    # Backwards-compat alias used by older tests
    @property
    def _game_time_label_compat(self) -> QLabel:
        return self._game_time_label

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.show()
        self._game_time_label.setText(_fmt_game_time(snapshot.game_time))
        by_name = {o.name: o for o in snapshot.objectives}
        for name, row in self._rows.items():
            row.update_from(by_name.get(name), snapshot.game_time)
