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

import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from . import styles

if TYPE_CHECKING:
    from ..jungle_timeline import JungleTimelineEngine
    from ..lcda.objectives import ObjectiveTimer


# Canonical camp positions on Summoner's Rift, normalized 0..1.
# Origin = top-left of the minimap image. Order Nexus is at bottom-left,
# Chaos Nexus at top-right.
#
# Coordinates derived from Riot's in-game world coords (~14882x14882
# game units) divided by map size, with Y inverted (game Y=0 is bottom,
# minimap Y=0 is top). Cross-checked against the in-game minimap.
CAMP_POSITIONS: dict[str, tuple[float, float]] = {
    # Order side (blue side) — bottom-left jungle quadrants
    "order_blue_buff": (0.276, 0.451),  # top-side jungle (NE of base)
    "order_red_buff":  (0.516, 0.711),  # bot-side jungle (SE of base)
    "order_gromp":     (0.146, 0.439),
    "order_wolves":    (0.253, 0.563),
    "order_raptors":   (0.464, 0.634),
    "order_krugs":     (0.566, 0.831),
    "order_scuttle":   (0.420, 0.320),  # top-river scuttle (closer to Baron pit)
    # Chaos side (red side) — top-right jungle quadrants
    # Mirror of Order through (0.5, 0.5).
    "chaos_blue_buff": (0.724, 0.549),
    "chaos_red_buff":  (0.484, 0.289),
    "chaos_gromp":     (0.854, 0.561),
    "chaos_wolves":    (0.747, 0.437),
    "chaos_raptors":   (0.536, 0.366),
    "chaos_krugs":     (0.434, 0.169),
    "chaos_scuttle":   (0.580, 0.680),  # bot-river scuttle (closer to Drake pit)
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
# Drake spawns bot-river (south-east of map center); Void Grubs / Herald /
# Baron all share the top-river pit (north-west of center) — only one is
# alive at a time, so the layer picks the most relevant one to render.
OBJECTIVE_POSITIONS: dict[str, tuple[float, float]] = {
    "Dragon":    (0.663, 0.703),
    "VoidGrubs": (0.336, 0.297),
    "Herald":    (0.336, 0.297),
    "Baron":     (0.336, 0.297),
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
        # Wall-clock anchor for interpolating game_time between LCDA polls.
        # LCDA refreshes every ~1-2s, but the scheduler repaints at 1 Hz.
        # Between snapshots we extrapolate from the last known game_time so
        # countdowns tick smoothly instead of freezing for up to 2 seconds.
        self._tick_wall_anchor: float = time.monotonic()

        # Pure read-only overlay — no clicks, no markers, only countdowns.
        # Mouse events pass through to whatever's behind (the in-game
        # minimap), so right-clicks on the minimap still issue commands.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # Team rotation: LoL renders the minimap with the active player's
        # base at the bottom. For an Order (blue-side) player our hardcoded
        # CAMP_POSITIONS already match the rendered orientation. For a Chaos
        # player the minimap is mirrored 180°, so each (nx, ny) flips to
        # (1-nx, 1-ny). Set via ``set_team`` from the parent.
        self._flip = False

        # Cache the font + colors so paintEvent does zero allocations
        # for static style state (the only per-frame allocation is the
        # QPainter itself, which Qt requires). Pixel size keeps the
        # countdown unobtrusive on top of the in-game minimap markers.
        self._font = QFont()
        self._font.setPixelSize(11)
        self._font.setBold(True)
        # All timer text in pure white — confidence used to drive a colour
        # gradient but the in-game minimap is busy enough that white reads
        # cleanest. Alpha 255 keeps it crisp; the upstream blink threshold
        # still fades imminent spawns on alternate ticks.
        white = QColor(255, 255, 255, 255)
        self._color_high = white
        self._color_mid  = white
        self._color_low  = white

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
        self._tick_wall_anchor = time.monotonic()
        self.update()

    def set_team(self, team: str) -> None:
        """Tell the layer which side the active player is on.

        Order/blue → no flip; Chaos/red → flip 180°. Anything else
        (empty / unknown) is treated as Order so we don't accidentally
        invert when team data is missing."""
        flip = (team or "").upper() == "CHAOS"
        if flip != self._flip:
            self._flip = flip
            self.update()

    # -- internals --------------------------------------------------------

    def _on_tick(self) -> None:
        self._blink_phase = 1 - self._blink_phase
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        """Read-only overlay — paints countdown text only, no markers
        or glyphs. Camps with no active timer paint nothing; the user
        sees the bare in-game minimap underneath."""
        try:
            states = self._engine.states()
        except Exception:  # noqa: BLE001 — paint must never crash UI
            return

        # During loading screens LCDA reports game_time=0 even though
        # we're getting snapshots. Don't paint anything until the actual
        # game clock has started ticking.
        if self._objective_game_time < 1.0:
            return

        # Wall-clock interpolation: extrapolate game_time forward from
        # the last LCDA snapshot so the countdown ticks every paint
        # frame instead of freezing between 1-2s LCDA polls.
        elapsed_since_tick = time.monotonic() - self._tick_wall_anchor
        # Cap at 5s so a stalled LCDA pipe doesn't run timers wildly past
        # actual game state.
        elapsed_since_tick = min(5.0, max(0.0, elapsed_since_tick))
        interpolated_game_time = self._objective_game_time + elapsed_since_tick

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(self._font)
            metrics = painter.fontMetrics()
            # Inset 4% on each side so coords map to the playable map area,
            # not the UI-decoration border around the minimap container.
            full = self.rect()
            inset = max(4, int(min(full.width(), full.height()) * 0.04))
            rect = full.adjusted(inset, inset, -inset, -inset)

            def _xy(nx: float, ny: float) -> tuple[float, float]:
                """Apply 180° flip when the active player is on Chaos side."""
                if self._flip:
                    return (1.0 - nx, 1.0 - ny)
                return (nx, ny)

            # Camp countdowns — text only, centered at the camp anchor.
            for camp_id, (nx, ny) in CAMP_POSITIONS.items():
                state = states.get(camp_id)
                if state is None:
                    continue
                # Re-derive remaining from next_spawn_at + interpolated time
                # so the countdown ticks smoothly between LCDA polls.
                remaining = max(0.0, state.next_spawn_at - interpolated_game_time)
                if state.state == "alive" or remaining <= 0.5:
                    continue
                if remaining <= self.BLINK_THRESHOLD_S and self._blink_phase:
                    continue
                text = _format_mmss(remaining)
                painter.setPen(self._color_for(state.confidence))
                fx, fy = _xy(nx, ny)
                anchor = map_to_screen(rect, fx, fy)
                text_w = metrics.horizontalAdvance(text)
                text_h = metrics.height()
                painter.drawText(
                    QPoint(anchor.x() - text_w // 2, anchor.y() + text_h // 3),
                    text,
                )

            # Major objective countdowns. Baron and Herald share the
            # same pit on the actual map (only one alive at a time), so
            # render whichever has the smaller remaining time at this
            # tick — the other isn't relevant yet.
            obj_remaining: dict[str, float] = {}
            for obj_name in OBJECTIVE_POSITIONS:
                obj = self._objectives.get(obj_name)
                if obj is None or obj.next_spawn_seconds is None:
                    continue
                rem = obj.remaining(interpolated_game_time)
                if rem is None or rem <= 0.5:
                    continue
                obj_remaining[obj_name] = rem

            # Three objectives share the Baron pit (Void Grubs → Herald
            # → Baron). Only one is alive at a time, so render the one
            # closest to spawning.
            BARON_PIT = {"VoidGrubs", "Herald", "Baron"}
            baron_pit_choice: str | None = None
            for cand in BARON_PIT:
                if cand in obj_remaining and (
                    baron_pit_choice is None
                    or obj_remaining[cand] < obj_remaining[baron_pit_choice]
                ):
                    baron_pit_choice = cand

            for obj_name, (nx, ny) in OBJECTIVE_POSITIONS.items():
                if obj_name not in obj_remaining:
                    continue
                # Skip the losers of the Baron-pit triple-share.
                if obj_name in BARON_PIT and obj_name != baron_pit_choice:
                    continue
                remaining = obj_remaining[obj_name]
                text = _format_mmss(remaining)
                painter.setPen(self._color_high)
                fx, fy = _xy(nx, ny)
                anchor = map_to_screen(rect, fx, fy)
                text_w = metrics.horizontalAdvance(text)
                text_h = metrics.height()
                painter.drawText(
                    QPoint(anchor.x() - text_w // 2, anchor.y() + text_h // 3),
                    text,
                )
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
