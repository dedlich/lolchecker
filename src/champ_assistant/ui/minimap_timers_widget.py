"""Floating mini-widget with Dragon/Baron/Herald spawn timers in a tight
horizontal pill, designed to live on or next to the in-game minimap.

LCDA exposes the kill events for these three objectives, so we get auto-
updating respawn timers without the user having to click. Compact format:

    🐉 1:50   👑 8:50   👁 —

For the side-jungle camps (Red/Blue/Gromp/Krugs/Wolves/Scuttle/Voidgrubs)
LCDA does NOT expose enemy clears, so a future iteration will let the
user click an icon to start a manual countdown. v0.11.7 ships only the
auto-tracked trio - which is what already makes the biggest difference.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel

from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget

OBJECTIVE_GLYPHS = {
    "Dragon": "🐉",
    "Baron":  "👑",
    "Herald": "👁",
}


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "UP"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    return f"{minutes:d}:{sec:02d}"


class MinimapTimersWidget(FloatingWidget):
    KEY = "minimap_timers"
    DEFAULT_POS = (1280, 720)  # roughly above the minimap on a 1080p screen
    DEFAULT_SIZE = (260, 36)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame[panel='true'] {{ background-color: rgba(11, 15, 20, 220);"
            f" border: 1px solid {styles.BORDER}; border-radius: {styles.RADIUS}px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(10)

        self._cells: dict[str, QLabel] = {}
        for name in ("Dragon", "Baron", "Herald"):
            cell = QLabel("")
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell.setStyleSheet(
                f"color: {styles.TEXT_PRIMARY};"
                " font-family: SF Mono, Consolas, monospace;"
                " font-size: 12px; font-weight: 700;"
            )
            self._cells[name] = cell
            layout.addWidget(cell, 1)

        self.hide()

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.show()
        by_name = {o.name: o for o in snapshot.objectives}
        for name, cell in self._cells.items():
            obj = by_name.get(name)
            text = self._cell_text(name, obj, snapshot.game_time)
            cell.setText(text)
            cell.setStyleSheet(self._cell_style(obj, snapshot.game_time))

    @staticmethod
    def _cell_text(name: str, obj: ObjectiveTimer | None, game_time: float) -> str:
        glyph = OBJECTIVE_GLYPHS.get(name, "•")
        if obj is None:
            return f"{glyph} —"
        return f"{glyph} {_fmt(obj.remaining(game_time))}"

    @staticmethod
    def _cell_style(obj: ObjectiveTimer | None, game_time: float) -> str:
        base = (
            "font-family: SF Mono, Consolas, monospace;"
            " font-size: 12px; font-weight: 700;"
        )
        if obj is None:
            return f"color: {styles.TEXT_DISABLED}; {base}"
        rem = obj.remaining(game_time)
        if rem is None:
            return f"color: {styles.TEXT_DISABLED}; {base}"
        if rem <= 0:
            return f"color: {styles.SUCCESS}; {base}"
        if rem <= 30:
            return f"color: {styles.WARNING}; {base}"
        if rem <= 60:
            return f"color: {styles.ACCENT}; {base}"
        return f"color: {styles.TEXT_PRIMARY}; {base}"
