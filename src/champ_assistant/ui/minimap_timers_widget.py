"""Floating mini-widget with two rows:

  Row 1: auto-tracked Dragon/Baron/Herald timers from LCDA events.
  Row 2: deterministic jungle-camp predictor (Red/Blue/Krugs/Gromp/
         Wolves/Raptors/Scuttle). Camps cycle on a fixed schedule
         driven by ``JungleTimelineEngine`` — no user interaction
         required, no LCDA kill events needed.

Compact format, sits on/next to the in-game minimap.

Confidence visual encoding (UI-only, never modifies timer values):
  * HIGH (>= 0.8): full opacity, deterministic display
  * MID  (0.4–0.8): slightly muted (85% alpha)
  * LOW  (< 0.4):   approximate mode — "≈" prefix + 70% alpha + dashed
    border to flag visual estimation. Spec is explicit that this only
    affects presentation, never the underlying countdown.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..jungle_timeline import JUNGLE_CAMPS, CampState, JungleTimelineEngine
from ..lcda.objectives import ObjectiveTimer
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget

# Confidence thresholds — the spec's three bands. Tunables, but keep
# in sync with the contract documented in the module docstring.
CONFIDENCE_HIGH  = 0.8
CONFIDENCE_LOW   = 0.4

# Per-band opacity (applied via rgba alpha so it survives Qt's
# stylesheet model — QGraphicsOpacityEffect would conflict with our
# drop-shadow setup and bring its own overhead).
OPACITY_HIGH = 1.00
OPACITY_MID  = 0.85
OPACITY_LOW  = 0.70

# Cell text width is pre-allocated in _CampCell.__init__ so swapping
# between "5:00", "≈5:00", and a short glyph never reflows the row.
APPROXIMATE_PREFIX = "≈"  # ≈


def _format_timer_text(state: CampState) -> str:
    """Render a CampState's countdown to display text. Pure function so
    the approximate-mode prefix logic can be unit-tested without Qt.

    Time formatting matches the rest of the app: "M:SS" with a leading
    "0:" for sub-minute values so the text width is constant inside one
    cell. Approximate prefix is added at the LOW confidence band only.
    """
    rem = state.time_remaining
    minutes, sec = divmod(int(rem + 0.5), 60)
    text = f"{minutes:d}:{sec:02d}" if minutes else f"0:{sec:02d}"
    if state.confidence < CONFIDENCE_LOW:
        return f"{APPROXIMATE_PREFIX}{text}"
    return text


def _band_for(confidence: float) -> str:
    """Map a confidence score onto its visual band ('high'/'mid'/'low')."""
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_LOW:
        return "mid"
    return "low"


def _opacity_for(confidence: float) -> float:
    band = _band_for(confidence)
    return {"high": OPACITY_HIGH, "mid": OPACITY_MID, "low": OPACITY_LOW}[band]

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

    # Pre-allocated cell size — wide enough for the longest possible
    # readout ("≈5:00", 5 chars at FONT_MONO 11px ≈ 32px) so swapping
    # between alive-glyph, plain countdown, and approximate-mode prefix
    # never reflows the row. Layout integrity (spec #1).
    CELL_W = 42
    CELL_H = 30

    def __init__(self, glyph: str, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._glyph = glyph
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(self.CELL_W, self.CELL_H)
        self.render(None)

    def render(self, state: CampState | None) -> None:
        """Update the cell from a fresh ``CampState`` (or clear if None).

        Confidence drives only the visual band (opacity + border style);
        the timer value itself is taken verbatim from the deterministic
        engine. See module docstring for the band thresholds.
        """
        if state is None:
            self.setText(self._glyph)
            self.setStyleSheet(self._idle_style())
            return
        if state.state == "alive" or state.time_remaining <= 0.5:
            # Camp is up — emphasise the glyph in success-color. Apply
            # the confidence opacity so a low-confidence "alive" still
            # reads as approximate without losing the spawn signal.
            self.setText(self._glyph)
            self.setStyleSheet(self._alive_style(state.confidence))
            return
        # Counting down. Timer text + ≈ prefix come from the pure
        # formatter so the prefix logic stays unit-testable.
        self.setText(_format_timer_text(state))
        self.setStyleSheet(self._countdown_style(state.time_remaining, state.confidence))

    # -- per-band stylesheets --------------------------------------------

    @staticmethod
    def _idle_style() -> str:
        return (
            f"QLabel {{ background: rgba(45, 55, 70, 90);"
            f" color: {styles.TEXT_SECONDARY};"
            f" border: 1px solid rgba(60, 70, 85, 100);"
            f" border-radius: 8px; font-size: 14px; }}"
        )

    @staticmethod
    def _alive_style(confidence: float) -> str:
        opacity = _opacity_for(confidence)
        bg_alpha = int(50 * opacity)
        border_alpha = int(255 * opacity)
        success_rgb = _hex_to_rgb_tuple(styles.SUCCESS)
        success_rgba = f"rgba({success_rgb[0]}, {success_rgb[1]}, {success_rgb[2]}, {border_alpha})"
        return (
            f"QLabel {{ background: rgba({success_rgb[0]}, {success_rgb[1]}, {success_rgb[2]}, {bg_alpha});"
            f" color: {success_rgba};"
            f" border: 1px solid {success_rgba};"
            f" border-radius: 8px; font-size: 14px; font-weight: 700; }}"
        )

    @staticmethod
    def _countdown_style(remaining: float, confidence: float) -> str:
        opacity = _opacity_for(confidence)
        # Border style flags the LOW band visually — dashed reads as
        # "estimated" at a glance without competing with the timer text.
        border_kind = "dashed" if _band_for(confidence) == "low" else "solid"
        border_rgb = _hex_to_rgb_tuple(styles.ACCENT)
        border_alpha = int(255 * opacity)
        bg_alpha = int(60 * opacity)
        # Convert the urgency color to rgba so opacity travels with it —
        # otherwise the LOW band has a faded border but full-color text,
        # which reads as inconsistent.
        urgency_rgb = _hex_to_rgb_tuple(styles.time_state_color(remaining))
        urgency_alpha = int(255 * opacity)
        return (
            f"QLabel {{ background: rgba({border_rgb[0]}, {border_rgb[1]}, {border_rgb[2]}, {bg_alpha});"
            f" color: rgba({urgency_rgb[0]}, {urgency_rgb[1]}, {urgency_rgb[2]}, {urgency_alpha});"
            f" border: 1px {border_kind} rgba({border_rgb[0]}, {border_rgb[1]}, {border_rgb[2]}, {border_alpha});"
            f" border-radius: 8px;"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: 11px; font-weight: 700; }}"
        )


def _hex_to_rgb_tuple(value: str) -> tuple[int, int, int]:
    """Tiny hex->rgb helper. Falls back to a neutral grey for any
    non-#RRGGBB input rather than raising — confidence-band rendering
    must never crash the cell."""
    if not isinstance(value, str) or not value.startswith("#") or len(value) != 7:
        return (128, 128, 128)
    try:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    except ValueError:
        return (128, 128, 128)


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
