"""Floating mini-widget with two rows:

  Row 1: auto-tracked Dragon/Baron/Herald timers from LCDA events.
  Row 2: manual jungle camps (Red/Blue/Krugs/Gromp/Wolves/Raptors/Scuttle).
         LCDA does not expose enemy clears, so the user clicks a camp to
         start its respawn countdown. Right-click resets.

Compact format, sits on/next to the in-game minimap.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
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
        self.setFixedSize(38, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(
            f"{name} ({int(cooldown)}s) — Linksklick: Timer start · Rechtsklick: reset"
        )
        self._idle_style = (
            f"QToolButton {{ background: rgba(45, 55, 70, 90);"
            f" color: {styles.TEXT_SECONDARY};"
            f" border: 1px solid rgba(60, 70, 85, 100);"
            f" border-radius: 8px; font-size: 14px; padding: 0; }}"
            f" QToolButton:hover {{ background: rgba(91, 168, 255, 60);"
            f" color: {styles.TEXT_PRIMARY};"
            f" border-color: {styles.ACCENT}; }}"
        )
        self._active_style = (
            f"QToolButton {{ background: rgba(91, 168, 255, 100);"
            f" color: {styles.TEXT_PRIMARY};"
            f" border: 1px solid {styles.ACCENT};"
            f" border-radius: 8px; font-size: 11px;"
            f" font-weight: 700; font-family: {styles.FONT_MONO}; padding: 0; }}"
        )
        self.setStyleSheet(self._idle_style)

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
            self.setStyleSheet(self._idle_style)
            return
        rem = max(0.0, (self.cast_at + self.cooldown) - game_time)
        if rem <= 0:
            self.cast_at = None
            self.setText(self._glyph)
            self.setStyleSheet(self._idle_style)
            return
        minutes, sec = divmod(int(rem + 0.5), 60)
        label = f"{minutes:d}:{sec:02d}" if minutes else f"{sec:d}"
        self.setText(label)
        self.setStyleSheet(self._active_style)


class MinimapTimersWidget(FloatingWidget):
    KEY = "minimap_timers"
    DEFAULT_POS = (1280, 600)  # above the minimap on a 1080p screen
    DEFAULT_SIZE = (332, 84)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(
            f"QFrame[panel='true'] {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"  stop:0 rgba(20, 26, 34, 180), stop:1 rgba(10, 14, 20, 180));"
            f" border: 1px solid rgba(60, 70, 85, 200);"
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
            # QToolButton.clicked emits a bool (checked-state). The captured
            # button has to be a default in a *positional* slot AFTER the
            # signal arg, otherwise Qt's positional bool overrides our
            # default and we end up calling .mark_used on False.
            btn.clicked.connect(lambda _checked=False, b=btn: self._on_camp_click(b))
            self._camps.append(btn)
            bottom.addWidget(btn)
        outer.addLayout(bottom)

        # Camp timers count down via the central RenderScheduler's 1 Hz
        # tick — see ``connect_scheduler`` below. This widget no longer
        # owns its own QTimer (P5: state-driven UI updates).
        self._latest_game_time = 0.0

        self.hide()

    def connect_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        """Hook the central 1 Hz tick. Called by __main__ at startup
        once the scheduler has been instantiated."""
        scheduler.tick.connect(self._refresh_camps)

    # -- public API -------------------------------------------------------

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.fade_appear()
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
            " font-variant-numeric: tabular-nums;"  # P2: stable digit width
        )
        rem = obj.remaining(game_time) if obj is not None else None
        return f"color: {styles.time_state_color(rem)}; {base}"
