"""Scoreboard-scoped overlay — shows gold-diff panel only while the
in-game scoreboard is detected as visible by the vision subsystem.

Architecture
============
* Pure View. Subscribes to ``state_store`` for ``scoreboard_visible``
  and ``lcda_snapshot``; computes gold diff via the pure function in
  ``game.gold_diff_service``. Doesn't own state.
* Floating top-level window styled to sit unobtrusively. When
  scoreboard_visible flips True, fade-in via the standard fade_appear
  helper. When False, the widget is simply hidden — internal data
  (the SpellTracker timers shown elsewhere) is unaffected.
* No reparenting — does not absorb the existing SummonerTracker. The
  spec is explicit ("DO NOT introduce duplicate timer systems") so
  spell timers stay in their existing widget; this overlay only
  surfaces the gold diff.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..game.gold_diff_service import LANE_ORDER, compute_team_gold_diff
from . import styles
from .floating_widget import FloatingWidget

IconLookup = Callable[[str], "QPixmap | None"]
LANE_ICON_SIZE = 22

if TYPE_CHECKING:
    from ..state_store import StateStore


def _format_gold_delta(value: int) -> str:
    """Format with explicit sign so positive vs zero are visually
    distinct (``+0`` vs ``0`` vs ``-1250``). Spec: integer only."""
    if value > 0:
        return f"+{value}"
    return str(value)


def _color_for_delta(value: int) -> str:
    if value > 0:
        return styles.SUCCESS
    if value < 0:
        return styles.DANGER
    return styles.TEXT_MUTED


class GoldDifferencePanel(FloatingWidget):
    """Tab-scoreboard-style gold readout.

    Layout mirrors the in-game TAB scoreboard:

      [BLUE_TOTAL]   ◀ 1928 ▶   [RED_TOTAL]     header
      [blue_icon]    ◀  1383    [red_icon]      per matchup
      [blue_icon]       825  ▶  [red_icon]
      ...

    Triangle direction encodes who's ahead in that lane / globally.
    Numbers are absolute magnitudes (unsigned) — the arrow does the
    sign work, matching the in-game style. Color shifts toward blue
    or red depending on lead direction.

    Hidden by default. Made visible by the controlling code below
    when ``state_store.scoreboard_visible`` is True.
    """
    KEY = "gold_diff_panel"
    DEFAULT_POS = (760, 80)
    DEFAULT_SIZE = (380, 220)

    BLUE_COLOR = "#3CA0E0"   # Riot's ORDER blue
    RED_COLOR  = "#D04040"   # Riot's CHAOS red

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(styles.floating_panel_stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_TIGHT + 2,
            styles.SPACING_WIDE, styles.SPACING_TIGHT + 2,
        )
        outer.setSpacing(3)

        # Header row: blue total | arrow + delta | red total.
        header = QHBoxLayout()
        header.setSpacing(styles.SPACING_GRID + 2)

        self._blue_total = QLabel("0")
        self._blue_total.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._blue_total.setStyleSheet(self._team_total_stylesheet(self.BLUE_COLOR))
        header.addWidget(self._blue_total, 1)

        self._team_arrow = QLabel("◆ 0")
        self._team_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._team_arrow.setStyleSheet(self._team_delta_stylesheet(0))
        header.addWidget(self._team_arrow, 0)

        self._red_total = QLabel("0")
        self._red_total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._red_total.setStyleSheet(self._team_total_stylesheet(self.RED_COLOR))
        header.addWidget(self._red_total, 1)

        outer.addLayout(header)

        # Per-lane rows: [blue icon/name] | [arrow + delta] | [red icon/name]
        # Champion icons preferred; falls back to champion name text when
        # the icon-lookup callable hasn't been provided yet.
        self._icon_lookup: IconLookup | None = None
        self._lane_rows: dict[str, dict] = {}
        for lane in LANE_ORDER:
            lane_row = QHBoxLayout()
            lane_row.setSpacing(styles.SPACING_GRID)

            blue_cell = QLabel("")
            blue_cell.setFixedHeight(LANE_ICON_SIZE)
            blue_cell.setMinimumWidth(LANE_ICON_SIZE * 4)
            blue_cell.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            blue_cell.setStyleSheet(self._lane_label_stylesheet(self.BLUE_COLOR))
            lane_row.addWidget(blue_cell, 1)

            mid_cell = QLabel("0")
            mid_cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mid_cell.setMinimumWidth(80)
            mid_cell.setStyleSheet(self._lane_delta_stylesheet(0))
            lane_row.addWidget(mid_cell, 0)

            red_cell = QLabel("")
            red_cell.setFixedHeight(LANE_ICON_SIZE)
            red_cell.setMinimumWidth(LANE_ICON_SIZE * 4)
            red_cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            red_cell.setStyleSheet(self._lane_label_stylesheet(self.RED_COLOR))
            lane_row.addWidget(red_cell, 1)

            self._lane_rows[lane] = {
                "blue": blue_cell, "mid": mid_cell, "red": red_cell,
                "row": lane_row,
            }
            outer.addLayout(lane_row)
        self._set_lanes_visible(False)
        self.hide()

    # -- public API ------------------------------------------------------

    def set_icon_lookup(self, lookup: IconLookup) -> None:
        """Wire the champion-name → QPixmap callable. Called once after
        DataDragon icons are loaded. Re-renders the current state if a
        gold diff has already been set."""
        self._icon_lookup = lookup

    def set_diff(self, gold_diff: dict) -> None:
        """Render the GoldDiff dict (see gold_diff_service.GoldDiff).
        Defensive on missing keys — older snapshots without
        blue_total/lane_champions degrade to text-only labels."""
        blue_total = int(gold_diff.get("blue_total", 0))
        red_total = int(gold_diff.get("red_total", 0))
        team_delta = blue_total - red_total

        self._blue_total.setText(f"{blue_total:,}".replace(",", "."))
        self._red_total.setText(f"{red_total:,}".replace(",", "."))
        self._team_arrow.setText(self._delta_text(team_delta))
        self._team_arrow.setStyleSheet(self._team_delta_stylesheet(team_delta))

        lane_breakdown = gold_diff.get("lane_breakdown") or {}
        lane_champions = gold_diff.get("lane_champions") or {}
        if not lane_breakdown:
            self._set_lanes_visible(False)
            return

        for lane, cells in self._lane_rows.items():
            delta = int(lane_breakdown.get(lane, 0))
            blue_name, red_name = lane_champions.get(lane, ("", ""))
            self._render_side_cell(cells["blue"], blue_name, lane.upper())
            self._render_side_cell(cells["red"], red_name, lane.upper())
            cells["mid"].setText(self._delta_text(delta))
            cells["mid"].setStyleSheet(self._lane_delta_stylesheet(delta))
        self._set_lanes_visible(True)

    def _render_side_cell(self, cell: QLabel, champ_name: str, lane_label: str) -> None:
        """Per-side cell: prefer champion icon, fall back to name text,
        last-resort fall back to lane label."""
        cell.setPixmap(QPixmap())  # clear any prior icon
        if champ_name and self._icon_lookup is not None:
            pix = self._icon_lookup(champ_name)
            if pix is not None and not pix.isNull():
                scaled = pix.scaled(
                    LANE_ICON_SIZE, LANE_ICON_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cell.setPixmap(scaled)
                cell.setText("")
                return
        cell.setText(champ_name or lane_label)

    def _set_lanes_visible(self, on: bool) -> None:
        for cells in self._lane_rows.values():
            cells["blue"].setVisible(on)
            cells["mid"].setVisible(on)
            cells["red"].setVisible(on)

    # -- styling helpers -------------------------------------------------

    @staticmethod
    def _delta_text(value: int) -> str:
        """Triangle + magnitude. Direction encodes the sign so the
        number itself stays unsigned (matches in-game scoreboard)."""
        if value > 0:
            return f"◀ {value:,}".replace(",", ".")  # blue ahead
        if value < 0:
            return f"{abs(value):,} ▶".replace(",", ".")  # red ahead
        return "◆ 0"

    @staticmethod
    def _team_total_stylesheet(color: str) -> str:
        return (
            f"color: {color};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_BODY}px; font-weight: 700;"
            " letter-spacing: 0.4px;"
        )

    @classmethod
    def _team_delta_stylesheet(cls, value: int) -> str:
        if value > 0:
            color = cls.BLUE_COLOR
        elif value < 0:
            color = cls.RED_COLOR
        else:
            color = styles.TEXT_MUTED
        return (
            f"color: {color};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )

    @staticmethod
    def _lane_label_stylesheet(color: str) -> str:
        return (
            f"color: {color};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 0.4px;"
        )

    @classmethod
    def _lane_delta_stylesheet(cls, value: int) -> str:
        if value > 0:
            color = cls.BLUE_COLOR
        elif value < 0:
            color = cls.RED_COLOR
        else:
            color = styles.TEXT_MUTED
        return (
            f"color: {color};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
        )


class ScoreboardOverlayController:
    """Owns the GoldDifferencePanel + the state-store subscription
    that drives it. Held as an instance on the main app so the
    subscription stays alive for the session.

    No singleton — this is constructed once in __main__ and registered
    with the LifecycleManager via its ``stop`` method.
    """

    def __init__(
        self,
        *,
        state_store: "StateStore",
        panel: GoldDifferencePanel,
        champion_tags: dict[str, list[str]] | None = None,
    ) -> None:
        self._store = state_store
        self._panel = panel
        # Champion-tag lookup is optional: when provided, the gold-diff
        # service attempts the lane-breakdown heuristic. Absent or
        # empty → only team-totals are shown.
        self._champion_tags = champion_tags or {}
        self._unsub = state_store.subscribe(self._on_state_change)
        # Apply initial state so the panel is correct before the first
        # update fires.
        self._on_state_change(state_store.get(), state_store.get())

    def update_champion_tags(self, tags: dict[str, list[str]]) -> None:
        """Update the champion-tag map (called once after DataDragon
        hydration finishes). Triggers a refresh of the displayed value
        if the panel is currently visible."""
        self._champion_tags = dict(tags)
        cur = self._store.get()
        if cur.scoreboard_visible:
            self._refresh_value(cur.lcda_snapshot)

    def _on_state_change(self, old, new) -> None:  # type: ignore[no-untyped-def]
        # Visibility gate
        if new.scoreboard_visible:
            if not self._panel.isVisible():
                self._panel.fade_appear()
            self._refresh_value(new.lcda_snapshot)
        else:
            if self._panel.isVisible():
                self._panel.hide()
            return

        # Re-render value when snapshot changes (and we're visible).
        if old.lcda_snapshot is not new.lcda_snapshot and new.scoreboard_visible:
            self._refresh_value(new.lcda_snapshot)

    def _refresh_value(self, snapshot) -> None:  # type: ignore[no-untyped-def]
        diff = compute_team_gold_diff(
            snapshot, champion_tags=self._champion_tags or None,
        )
        self._panel.set_diff(diff)

    def stop(self) -> None:
        """LifecycleManager-callable shutdown. Drops the subscription
        so a half-torn-down state store doesn't try to call back into
        this object."""
        try:
            self._unsub()
        except Exception:  # noqa: BLE001
            pass
