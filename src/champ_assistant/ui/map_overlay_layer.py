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

Camp layout
-----------
Both Order (blue side) and Chaos (red side) camps are tracked — 14
total (7 per side). order_scuttle anchors to the top-river spawn
position and chaos_scuttle to the bot-river position; this gives the
two scuttle camps their natural split-river placement on the minimap.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from . import styles

if TYPE_CHECKING:
    from ..jungle_timeline import JungleTimelineEngine
    from ..lcda.objectives import ObjectiveTimer


# Canonical camp positions on Summoner's Rift, normalized 0..1
# (origin = top-left of the minimap image; Order base is bottom-left,
# Chaos base is top-right). Coordinates derived from the in-game
# minimap with both sides represented (14 camps total).
CAMP_POSITIONS: dict[str, tuple[float, float]] = {
    # Order side (blue side) — bottom-left jungle
    "order_blue_buff": (0.22, 0.65),
    "order_red_buff":  (0.38, 0.76),
    "order_gromp":     (0.14, 0.53),
    "order_wolves":    (0.25, 0.53),
    "order_raptors":   (0.40, 0.67),
    "order_krugs":     (0.30, 0.83),
    "order_scuttle":   (0.43, 0.60),   # top-river scuttle spawn
    # Chaos side (red side) — top-right jungle
    "chaos_blue_buff": (0.78, 0.35),
    "chaos_red_buff":  (0.62, 0.24),
    "chaos_gromp":     (0.86, 0.47),
    "chaos_wolves":    (0.75, 0.47),
    "chaos_raptors":   (0.60, 0.33),
    "chaos_krugs":     (0.70, 0.17),
    "chaos_scuttle":   (0.57, 0.40),   # bot-river scuttle spawn
}

# Single-letter glyph drawn inside each camp marker.
CAMP_GLYPHS: dict[str, str] = {
    "order_red_buff":  "R",
    "order_blue_buff": "B",
    "order_gromp":     "G",
    "order_krugs":     "K",
    "order_raptors":   "P",   # P for raptoRs — R is taken by Red Buff
    "order_wolves":    "W",
    "order_scuttle":   "S",
    "chaos_red_buff":  "R",
    "chaos_blue_buff": "B",
    "chaos_gromp":     "G",
    "chaos_krugs":     "K",
    "chaos_raptors":   "P",
    "chaos_wolves":    "W",
    "chaos_scuttle":   "S",
}

# Per-camp marker tint. Buff camps get their canonical colors; small
# camps stay neutral so they don't compete for attention; scuttle gets
# the river's tint. Both sides share the same color scheme by camp type.
CAMP_COLORS: dict[str, str] = {
    "order_red_buff":  styles.DANGER,
    "order_blue_buff": styles.ACCENT,
    "order_gromp":     styles.TEXT_MUTED,
    "order_krugs":     styles.TEXT_MUTED,
    "order_raptors":   styles.TEXT_MUTED,
    "order_wolves":    styles.TEXT_MUTED,
    "order_scuttle":   styles.TIER_A,
    "chaos_red_buff":  styles.DANGER,
    "chaos_blue_buff": styles.ACCENT,
    "chaos_gromp":     styles.TEXT_MUTED,
    "chaos_krugs":     styles.TEXT_MUTED,
    "chaos_raptors":   styles.TEXT_MUTED,
    "chaos_wolves":    styles.TEXT_MUTED,
    "chaos_scuttle":   styles.TIER_A,
}

# Marker pixel size (radius). Picked so seven non-overlapping circles
# fit comfortably on the smallest minimap panel (110×110).
MARKER_RADIUS_PX = 9


# Major-objective pit positions on the SR minimap, normalized 0..1.
# Drake spawns bot-river; Baron + Herald share the top-river pit (Herald
# until ~14:00, Baron after — same position).
OBJECTIVE_POSITIONS: dict[str, tuple[float, float]] = {
    "Dragon": (0.42, 0.62),
    "Baron":  (0.58, 0.38),
    "Herald": (0.58, 0.38),
}

# Glyph + tint for the major-objective markers. Same paint pipeline
# as the camp markers, just a separate registry so the visual styling
# can drift independently if needed.
OBJECTIVE_GLYPHS: dict[str, str] = {
    "Dragon": "D",
    "Baron":  "B",
    "Herald": "H",
}

OBJECTIVE_COLORS: dict[str, str] = {
    "Dragon": styles.DANGER,    # red — Riot's elemental-drake palette varies, danger is a safe default
    "Baron":  styles.WARNING,   # gold/amber for Baron Nashor
    "Herald": styles.TIER_A,    # purple-ish for Herald
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
        # Latest LCDA-driven major-objective state. Set by parent via
        # ``set_objectives`` on every snapshot; rendered alongside the
        # camp markers in paintEvent.
        self._objectives: dict[str, "ObjectiveTimer"] = {}
        self._objective_game_time: float = 0.0

        # Click-to-arm: clicking near a camp position registers an
        # observed clear with the engine, starting the real respawn
        # countdown. The user explicitly wants kill-driven timers,
        # not predictive ones; this is the input path. Cursor changes
        # to a pointer to signal interactivity.
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "Klick auf ein Camp markiert es als beobachtet gekillt — "
            "Timer startet erst dann."
        )

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

    def set_objectives(
        self,
        objectives: dict[str, "ObjectiveTimer"],
        game_time: float,
    ) -> None:
        """Forward LCDA-derived major-objective state. Drake/Baron/
        Herald markers paint at their pit positions and show the
        respawn countdown when the engine reports them killed."""
        self._objectives = dict(objectives)
        self._objective_game_time = float(game_time)
        self.update()

    # -- internals --------------------------------------------------------

    def _on_tick(self) -> None:
        self._blink_phase = 1 - self._blink_phase
        self.update()

    # -- click handling --------------------------------------------------

    # Clicks within this many normalized units of a camp anchor count.
    # 0.10 in a 0..1 coord system ≈ 10% of the minimap edge — generous
    # enough on a 110×110 px panel that fingers don't have to be
    # surgically precise.
    CLICK_HIT_RADIUS = 0.10

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # type: ignore[override]
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        camp_id = self._camp_at(event.position().x(), event.position().y())
        if camp_id is None:
            return
        try:
            self._engine.register_clear(camp_id)
        except Exception:  # noqa: BLE001 — input handler must never crash UI
            return
        self.update()

    def _camp_at(self, px: float, py: float) -> str | None:
        """Find the camp whose anchor is closest to the click point,
        within ``CLICK_HIT_RADIUS`` (normalized). Returns the camp_id
        or None if no camp is in range."""
        rect = self.rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        nx = (px - rect.left()) / rect.width()
        ny = (py - rect.top()) / rect.height()
        best_id: str | None = None
        best_dist = self.CLICK_HIT_RADIUS
        for camp_id, (anchor_nx, anchor_ny) in CAMP_POSITIONS.items():
            dx = nx - anchor_nx
            dy = ny - anchor_ny
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_id = camp_id
        return best_id

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

            # First pass — always draw camp markers (filled circle +
            # glyph) at every position. Markers fade when the camp is
            # in alive sentinel (un-armed) so the user sees they
            # haven't started a timer yet, but still knows what's
            # clickable. Active timers get a brighter marker.
            for camp_id, (nx, ny) in CAMP_POSITIONS.items():
                state = states.get(camp_id)
                anchor = map_to_screen(rect, nx, ny)
                self._paint_marker(
                    painter, anchor, camp_id,
                    armed=state is not None and state.state != "alive",
                )

            # Second pass — countdown text overlay on top of markers
            # for camps with active timers.
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
                # Position the countdown text below the marker rather
                # than centered on it — the marker is now drawn at the
                # anchor point and the text sits underneath.
                text_w = metrics.horizontalAdvance(text)
                text_h = metrics.height()
                draw_pt = QPoint(
                    anchor.x() - text_w // 2,
                    anchor.y() + MARKER_RADIUS_PX + text_h - 2,
                )
                painter.drawText(draw_pt, text)

            # Third pass — major-objective markers (Drake/Baron/Herald)
            # at their pit positions. Same marker pipeline as camps;
            # countdown only renders when LCDA reports the objective
            # has been killed (next_spawn_seconds > 0).
            for obj_name, (nx, ny) in OBJECTIVE_POSITIONS.items():
                anchor = map_to_screen(rect, nx, ny)
                obj = self._objectives.get(obj_name)
                armed = (
                    obj is not None
                    and obj.next_spawn_seconds is not None
                    and obj.remaining(self._objective_game_time) is not None
                )
                self._paint_objective_marker(painter, anchor, obj_name, armed=bool(armed))
                if not armed or obj is None:
                    continue
                remaining = obj.remaining(self._objective_game_time)
                if remaining is None or remaining <= 0.5:
                    continue
                text = _format_mmss(remaining)
                painter.setPen(self._color_high)
                text_w = metrics.horizontalAdvance(text)
                text_h = metrics.height()
                draw_pt = QPoint(
                    anchor.x() - text_w // 2,
                    anchor.y() + MARKER_RADIUS_PX + text_h - 2,
                )
                painter.drawText(draw_pt, text)
        finally:
            painter.end()

    def _paint_objective_marker(
        self,
        painter: QPainter,
        anchor: QPoint,
        obj_name: str,
        *,
        armed: bool,
    ) -> None:
        """Same shape as camp markers but pulls from the OBJECTIVE_*
        registries. Rendered always so the player sees the pit even
        before the objective is first killed."""
        base_color = QColor(OBJECTIVE_COLORS.get(obj_name, styles.TEXT_MUTED))
        fill_alpha = 230 if armed else 140
        ring_alpha = 255 if armed else 180
        fill = QColor(base_color)
        fill.setAlpha(fill_alpha)
        ring = QColor(base_color)
        ring.setAlpha(ring_alpha)

        painter.setPen(ring)
        painter.setBrush(fill)
        painter.drawEllipse(
            anchor.x() - MARKER_RADIUS_PX,
            anchor.y() - MARKER_RADIUS_PX,
            MARKER_RADIUS_PX * 2,
            MARKER_RADIUS_PX * 2,
        )

        glyph = OBJECTIVE_GLYPHS.get(obj_name, "?")
        glyph_color = QColor("#FFFFFF")
        glyph_color.setAlpha(255 if armed else 200)
        painter.setPen(glyph_color)
        gm = painter.fontMetrics()
        gw = gm.horizontalAdvance(glyph)
        gh = gm.ascent()
        painter.drawText(
            QPoint(anchor.x() - gw // 2, anchor.y() + gh // 2 - 1),
            glyph,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_marker(
        self,
        painter: QPainter,
        anchor: QPoint,
        camp_id: str,
        *,
        armed: bool,
    ) -> None:
        """Draw a single camp marker — filled circle with a glyph
        letter. Dim alpha when un-armed (no timer running) so the user
        sees the camp is clickable but doesn't have an active count.
        Bright alpha when armed."""
        base_color = QColor(CAMP_COLORS.get(camp_id, styles.TEXT_MUTED))
        fill_alpha = 220 if armed else 140
        ring_alpha = 255 if armed else 180
        fill = QColor(base_color)
        fill.setAlpha(fill_alpha)
        ring = QColor(base_color)
        ring.setAlpha(ring_alpha)

        # Filled circle.
        painter.setPen(ring)
        painter.setBrush(fill)
        painter.drawEllipse(
            anchor.x() - MARKER_RADIUS_PX,
            anchor.y() - MARKER_RADIUS_PX,
            MARKER_RADIUS_PX * 2,
            MARKER_RADIUS_PX * 2,
        )

        # Glyph in the middle. White text reads on every camp color.
        glyph = CAMP_GLYPHS.get(camp_id, "?")
        glyph_color = QColor("#FFFFFF")
        glyph_color.setAlpha(255 if armed else 200)
        painter.setPen(glyph_color)
        glyph_metrics = painter.fontMetrics()
        gw = glyph_metrics.horizontalAdvance(glyph)
        gh = glyph_metrics.ascent()
        painter.drawText(
            QPoint(anchor.x() - gw // 2, anchor.y() + gh // 2 - 1),
            glyph,
        )
        # Reset brush so callers don't see it leaking into the next pass.
        painter.setBrush(Qt.BrushStyle.NoBrush)

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
