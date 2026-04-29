"""Transparent overlay layer that renders camp respawn countdowns at
their canonical Summoner's Rift positions inside a parent area.

Design notes
============
This is a self-contained QWidget that:

  * paints countdown text from the deterministic JungleTimelineEngine,
  * positions each camp via a normalized 0..1 coordinate dict so the
    layer can be resized to any size and the camps follow,
  * has zero internal timers — the parent connects ``RenderScheduler.tick``
    to ``advance_blink_phase()`` and that is the only refresh trigger,
  * never accepts mouse input — purely visual, pass-through to the
    parent widget.

What it does NOT do
-------------------
  * No layout-position persistence (Definition of Done: "Do not store
    layout positions"). The layer's geometry comes entirely from its
    parent.
  * No new top-level windows. Always a child widget.
  * No allocations in paintEvent — colors and fonts are cached.

Scuttle divergence from the original spec
------------------------------------------
The spec listed ``scuttle_top`` + ``scuttle_bot`` as separate entries.
Our ``JungleTimelineEngine`` tracks scuttle as a single camp (the two
spawns alternate; the engine doesn't model the alternation). Rather
than fake a top/bot split we'd then have to keep in sync with the
engine, we render scuttle once at the river center. Engine-side
scuttle alternation is a separate task if it's ever needed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from . import styles

if TYPE_CHECKING:
    from ..jungle_timeline import JungleTimelineEngine


# Canonical camp positions on Summoner's Rift, normalized 0..1
# (origin = top-left corner of the minimap area).
# Buffs are corner-anchored (bot-left = blue side red, top-right = red
# side blue); small camps follow the standard SR jungle layout.
CAMP_POSITIONS: dict[str, tuple[float, float]] = {
    "red_buff":  (0.18, 0.82),
    "blue_buff": (0.82, 0.18),
    "gromp":     (0.78, 0.28),
    "krugs":     (0.25, 0.90),
    "raptors":   (0.35, 0.70),
    "wolves":    (0.70, 0.35),
    "scuttle":   (0.50, 0.50),  # river center — single entry in the engine
}


def map_to_screen(rect: QRect, norm_x: float, norm_y: float) -> QPoint:
    """Convert a (norm_x, norm_y) camp position in [0..1] to a QPoint
    inside the given QRect. Pure function — testable without Qt
    runtime state."""
    return QPoint(
        int(rect.left() + rect.width() * norm_x),
        int(rect.top() + rect.height() * norm_y),
    )


def _format_mmss(seconds: float) -> str:
    """Render a positive seconds value as ``M:SS`` (no leading zero on
    minutes; matches the rest of the app's countdown formatting).
    Negative values clamp to 0."""
    secs = max(0, int(seconds + 0.5))
    minutes, remainder = divmod(secs, 60)
    return f"{minutes}:{remainder:02d}"


class MapOverlayLayer(QWidget):
    """Transparent layer painting camp countdowns over its parent area.

    Use:

        layer = MapOverlayLayer(engine, parent=minimap_panel)
        layer.connect_scheduler(scheduler)   # 1 Hz repaint cadence

    Geometry: the layer should be resized to fill its parent's "minimap
    area" — the parent's resizeEvent typically calls
    ``layer.setGeometry(parent.rect())``.
    """

    # Within this many seconds of spawn, blink the timer to draw the eye.
    BLINK_THRESHOLD_S = 5.0

    def __init__(
        self,
        engine: "JungleTimelineEngine",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._blink_phase: int = 0   # toggled by scheduler tick

        # Pure visual layer — no mouse interaction, events fall through.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Cache the font + colors so paintEvent does zero allocations
        # for static style state (the only per-frame allocation is the
        # QPainter itself, which Qt requires).
        self._font = QFont()
        self._font.setPointSize(styles.FS_CAPTION)
        self._font.setBold(True)
        self._color_high = self._rgba(styles.TEXT_PRIMARY, 255)
        self._color_mid  = self._rgba(styles.WARNING,      217)  # 0.85 * 255
        self._color_low  = self._rgba(styles.TEXT_MUTED,   178)  # 0.70 * 255

    # -- public API -------------------------------------------------------

    def connect_scheduler(self, scheduler) -> None:  # type: ignore[no-untyped-def]
        """Wire the central 1 Hz tick. The scheduler's tick toggles the
        blink phase + triggers a repaint — this is the only refresh
        path. No QTimer inside this widget."""
        scheduler.tick.connect(self._on_tick)

    # -- internals --------------------------------------------------------

    def _on_tick(self) -> None:
        self._blink_phase = 1 - self._blink_phase
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        try:
            states = self._engine.states()
        except Exception:  # noqa: BLE001 — paint must never crash UI
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(self._font)
            metrics = painter.fontMetrics()
            rect = self.rect()

            for camp_id, (nx, ny) in CAMP_POSITIONS.items():
                state = states.get(camp_id)
                if state is None:
                    continue

                # Hide the timer while the camp is alive (just spawned)
                # — that's the visual signal "camp is up". Once the
                # alive-grace window ends in the engine's model the
                # timer reappears with the full cycle countdown.
                if state.state == "alive" or state.time_remaining <= 0.5:
                    continue

                # Blink during last BLINK_THRESHOLD_S seconds — skip
                # drawing on alternate scheduler ticks.
                if (
                    state.time_remaining <= self.BLINK_THRESHOLD_S
                    and self._blink_phase
                ):
                    continue

                text = _format_mmss(state.time_remaining)
                painter.setPen(self._color_for(state.confidence))

                anchor = map_to_screen(rect, nx, ny)
                # Center the text on the anchor point.
                text_w = metrics.horizontalAdvance(text)
                text_h = metrics.height()
                # Vertical: y is the baseline; subtract ~1/4 height so
                # the visual centroid lands on anchor.y().
                draw_pt = QPoint(
                    anchor.x() - text_w // 2,
                    anchor.y() + text_h // 4,
                )
                painter.drawText(draw_pt, text)
        finally:
            painter.end()

    def _color_for(self, confidence: float) -> QColor:
        """Return one of the cached QColor instances. No allocation —
        the three colors are built once in __init__."""
        if confidence >= 0.8:
            return self._color_high
        if confidence >= 0.4:
            return self._color_mid
        return self._color_low

    @staticmethod
    def _rgba(hex_color: str, alpha: int) -> QColor:
        """Build a QColor from a #RRGGBB token + alpha byte. Used in
        __init__ to pre-build the three confidence-band colors."""
        c = QColor(hex_color)
        c.setAlpha(alpha)
        return c
