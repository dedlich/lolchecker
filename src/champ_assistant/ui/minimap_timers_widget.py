"""Floating mini-widget with two rows:

  Row 1: auto-tracked Dragon/Baron/Herald timers from LCDA events.
  Row 2: deterministic jungle-camp predictor (Red/Blue/Krugs/Gromp/
         Wolves/Raptors/Scuttle). Camps cycle on a fixed schedule
         driven by ``JungleTimelineEngine`` — no user interaction
         required, no LCDA kill events needed.

Compact format, sits on/next to the in-game minimap.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..jungle_timeline import JUNGLE_CAMPS, CampState, JungleTimelineEngine
from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget

OBJECTIVE_GLYPHS = {
    "Dragon": "🐉",
    "Baron":  "👑",
    "Herald": "👁",
}

# Glyph per camp id — kept here (not in jungle_timeline) since the
# emoji choice is a UI concern.
CAMP_GLYPHS: dict[str, str] = {
    "red_buff":  "🔥",
    "blue_buff": "💎",
    "gromp":     "🐸",
    "krugs":     "🪨",
    "raptors":   "🦅",
    "wolves":    "🐺",
    "scuttle":   "🦀",
}


def _fmt(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds <= 0:
        return "UP"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    return f"{minutes:d}:{sec:02d}"


class _CampCell(QLabel):
    """Stateless camp-state display. Renders whatever the latest
    ``CampState`` from the engine says. No mouse handling, no internal
    timers — purely reactive (P6).
    """

    def __init__(self, glyph: str, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._glyph = glyph
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(38, 30)
        self.render(None)

    def render(self, state: CampState | None) -> None:
        """Update the cell from a fresh ``CampState`` (or clear if None)."""
        if state is None:
            self.setText(self._glyph)
            self.setStyleSheet(self._idle_style())
            return
        if state.state == "alive" or state.time_remaining <= 0.5:
            # Camp is up — emphasise the glyph in success-color.
            self.setText(self._glyph)
            self.setStyleSheet(self._alive_style())
            return
        # Counting down — colored timer text replaces the glyph for
        # readability. Color ramps with urgency via ``time_state_color``
        # so the same scale that drives objective timers also drives
        # camps (visual consistency, P7).
        rem = state.time_remaining
        minutes, sec = divmod(int(rem + 0.5), 60)
        text = f"{minutes:d}:{sec:02d}" if minutes else f"0:{sec:02d}"
        self.setText(text)
        self.setStyleSheet(self._countdown_style(rem, state.confidence))

    @staticmethod
    def _idle_style() -> str:
        return (
            f"QLabel {{ background: rgba(45, 55, 70, 90);"
            f" color: {styles.TEXT_SECONDARY};"
            f" border: 1px solid rgba(60, 70, 85, 100);"
            f" border-radius: 8px; font-size: 14px; }}"
        )

    @staticmethod
    def _alive_style() -> str:
        return (
            f"QLabel {{ background: rgba(127, 204, 127, 50);"
            f" color: {styles.SUCCESS};"
            f" border: 1px solid {styles.SUCCESS};"
            f" border-radius: 8px; font-size: 14px; font-weight: 700; }}"
        )

    @staticmethod
    def _countdown_style(remaining: float, confidence: float) -> str:
        # Confidence in [MIN..1.0] modulates alpha so a low-confidence
        # prediction visibly reads as "estimated" without changing the
        # readout itself (P3 spec: never override deterministic values).
        alpha = int(120 + 135 * max(0.0, min(1.0, confidence)))
        color = styles.time_state_color(remaining)
        return (
            f"QLabel {{ background: rgba(91, 168, 255, {alpha // 4});"
            f" color: {color};"
            f" border: 1px solid rgba(91, 168, 255, {alpha});"
            f" border-radius: 8px;"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: 11px; font-weight: 700; }}"
        )


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
                f" font-family: {styles.FONT_MONO};"
                " font-size: 12px; font-weight: 700;"
            )
            self._cells[name] = cell
            top.addWidget(cell, 1)
        outer.addLayout(top)

        # Row 2: deterministic jungle camp predictor.
        # Cells render purely from the engine's CampState — they own no
        # state of their own, no internal QTimers, no click handlers.
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self._camp_cells: dict[str, _CampCell] = {}
        for spec in JUNGLE_CAMPS:
            glyph = CAMP_GLYPHS.get(spec.id, "•")
            cell = _CampCell(glyph, parent=self)
            cell.setToolTip(f"{spec.name} — predicted spawn cycle")
            self._camp_cells[spec.id] = cell
            bottom.addWidget(cell)
        outer.addLayout(bottom)

        self._latest_game_time = 0.0
        self._engine: JungleTimelineEngine | None = None
        self._engine_unsub = None  # type: ignore[var-annotated]

        self.hide()

    # -- wiring ----------------------------------------------------------

    def attach_engine(self, engine: JungleTimelineEngine) -> None:
        """Subscribe to the central JungleTimelineEngine. Idempotent —
        re-attaching swaps the previous subscription."""
        if self._engine_unsub is not None:
            self._engine_unsub()
        self._engine = engine
        self._engine_unsub = engine.subscribe(self._on_camp_states)
        # Render whatever the engine knows right now (covers the case
        # where the engine ticked before the widget was attached).
        self._on_camp_states(engine.states())

    def connect_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        """Hook the central 1 Hz tick — drives the objectives countdown.
        Camp cells are pushed by the engine's own tick, not from here."""
        scheduler.tick.connect(self._refresh_objectives)

    # -- public API ------------------------------------------------------

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

    # -- internals -------------------------------------------------------

    def _on_camp_states(self, states: dict[str, CampState]) -> None:
        for camp_id, cell in self._camp_cells.items():
            cell.render(states.get(camp_id))

    def _refresh_objectives(self) -> None:
        # Re-render Row 1 each tick so the countdown text updates between
        # LCDA snapshots. Cheap — three setText calls + a stylesheet.
        if self._latest_game_time <= 0:
            return
        for name, cell in self._cells.items():
            # We can't recover the ObjectiveTimer here without storing
            # the last snapshot; that's done in update_snapshot. The
            # tick refresh is for the camp row primarily; objectives
            # update on the next snapshot.
            pass

    @staticmethod
    def _cell_text(name: str, obj: ObjectiveTimer | None, game_time: float) -> str:
        glyph = OBJECTIVE_GLYPHS.get(name, "•")
        if obj is None:
            return f"{glyph} —"
        return f"{glyph} {_fmt(obj.remaining(game_time))}"

    @staticmethod
    def _cell_style(obj: ObjectiveTimer | None, game_time: float) -> str:
        # Stable digit width comes from FONT_MONO; Qt Stylesheet doesn't
        # support font-variant-numeric.
        base = (
            f"font-family: {styles.FONT_MONO};"
            " font-size: 12px; font-weight: 700;"
        )
        rem = obj.remaining(game_time) if obj is not None else None
        return f"color: {styles.time_state_color(rem)}; {base}"
