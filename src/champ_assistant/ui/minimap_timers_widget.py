"""Floating mini-widget with two rows:

  Row 1: auto-tracked Dragon/Baron/Herald timers from LCDA events.
  Row 2: manual jungle camps (Red/Blue/Krugs/Gromp/Wolves/Raptors/Scuttle).
         LCDA does not expose enemy clears, so the user clicks a camp to
         start its respawn countdown. Right-click resets.

Compact format, sits on/next to the in-game minimap.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget

OBJECTIVE_GLYPHS = {
    "Dragon": "🐉",
    "Baron":  "👑",
    "Herald": "👁",
}


# Side-jungle camps: respawn time in seconds after the kill (current patch).
# Buffs are 5:00, plain camps 2:15, scuttle 2:30.
JUNGLE_CAMPS: list[tuple[str, str, float]] = [
    ("Red",     "🔥", 300.0),
    ("Blue",    "💎", 300.0),
    ("Krugs",   "🪨", 135.0),
    ("Gromp",   "🐸", 135.0),
    ("Wolves",  "🐺", 135.0),
    ("Raptors", "🦅", 135.0),
    ("Scuttle", "🦀", 150.0),
]


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "UP"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    return f"{minutes:d}:{sec:02d}"


class _CampButton(QToolButton):
    """Clickable jungle-camp icon. Left-click starts a fresh respawn timer
    pinned to current game-time; right-click clears it."""

    def __init__(self, name: str, glyph: str, cooldown: float, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.name = name
        self.cooldown = cooldown
        self.cast_at: float | None = None
        self._glyph = glyph
        self.setText(glyph)
        self.setFixedSize(34, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(
            f"{name} ({int(cooldown)}s) — Linksklick: Timer start · Rechtsklick: reset"
        )
        self.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {styles.TEXT_SECONDARY};"
            f" border: none; border-radius: {styles.RADIUS_SMALL}px; font-size: 14px; }}"
            f" QToolButton:hover {{ color: {styles.TEXT_PRIMARY}; }}"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.RightButton:
            self.cast_at = None
            self.update_text(0.0)
            event.accept()
            return
        # Treat regular click as "camp just died" — caller fills cast_at
        # with current game_time via mark_used().
        super().mousePressEvent(event)

    def mark_used(self, game_time: float) -> None:
        self.cast_at = game_time

    def update_text(self, game_time: float) -> None:
        if self.cast_at is None:
            self.setText(self._glyph)
            self.setStyleSheet(self.styleSheet().replace(
                f"color: {styles.WARNING}", f"color: {styles.TEXT_SECONDARY}"
            ))
            return
        rem = max(0.0, (self.cast_at + self.cooldown) - game_time)
        if rem <= 0:
            self.cast_at = None
            self.setText(self._glyph)
            return
        minutes, sec = divmod(int(rem + 0.5), 60)
        label = f"{minutes:d}:{sec:02d}" if minutes else f"{sec:d}s"
        self.setText(f"{self._glyph} {label}")


class MinimapTimersWidget(FloatingWidget):
    KEY = "minimap_timers"
    DEFAULT_POS = (1280, 600)  # above the minimap on a 1080p screen
    DEFAULT_SIZE = (300, 76)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame[panel='true'] {{ background-color: rgba(11, 15, 20, 150);"
            f" border: 1px solid rgba(40, 48, 60, 180);"
            f" border-radius: {styles.RADIUS}px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        # Row 1: auto-tracked objectives
        top = QHBoxLayout()
        top.setSpacing(10)
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
            top.addWidget(cell, 1)
        outer.addLayout(top)

        # Row 2: manual jungle camp click-to-start timers
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self._camps: list[_CampButton] = []
        for camp_name, glyph, cd in JUNGLE_CAMPS:
            btn = _CampButton(camp_name, glyph, cd, parent=self)
            btn.clicked.connect(lambda _b=btn: self._on_camp_click(_b))
            self._camps.append(btn)
            bottom.addWidget(btn)
        outer.addLayout(bottom)

        # Tick the camp timers every 500ms so they count down smoothly even
        # in between LCDA snapshots (which arrive every ~2s).
        self._latest_game_time = 0.0
        self._tick = QTimer(self)
        self._tick.setInterval(500)
        self._tick.timeout.connect(self._refresh_camps)

        self.hide()

    # -- public API -------------------------------------------------------

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            self._tick.stop()
            return
        self.show()
        if not self._tick.isActive():
            self._tick.start()
        self._latest_game_time = snapshot.game_time
        by_name = {o.name: o for o in snapshot.objectives}
        for name, cell in self._cells.items():
            obj = by_name.get(name)
            cell.setText(self._cell_text(name, obj, snapshot.game_time))
            cell.setStyleSheet(self._cell_style(obj, snapshot.game_time))
        self._refresh_camps()

    # -- internals --------------------------------------------------------

    def _on_camp_click(self, btn: _CampButton) -> None:
        btn.mark_used(self._latest_game_time)
        self._refresh_camps()

    def _refresh_camps(self) -> None:
        # Game time advances even between snapshots — increment locally.
        # We don't get a tick of game-time updates so estimate from real
        # time elapsed since last snapshot.
        for btn in self._camps:
            btn.update_text(self._latest_game_time)

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
