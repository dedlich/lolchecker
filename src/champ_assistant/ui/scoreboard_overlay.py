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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..game.gold_diff_service import LANE_ORDER, compute_team_gold_diff
from . import styles
from .floating_widget import FloatingWidget

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
    """Single-line readout: TEAM GOLD DIFF: <value>.

    Hidden by default. Made visible by the controlling code below
    when ``state_store.scoreboard_visible`` is True. When hidden the
    overlay does no work — no polling, no rendering, no listeners
    fire (subscription is on state-store change, which only fires
    on actual updates).
    """
    KEY = "gold_diff_panel"
    DEFAULT_POS = (760, 80)   # top-center on a 1080p screen
    DEFAULT_SIZE = (320, 56)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(styles.floating_panel_stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            styles.SPACING_WIDE, styles.SPACING_TIGHT,
            styles.SPACING_WIDE, styles.SPACING_TIGHT,
        )
        outer.setSpacing(2)

        # Header row — always visible: TEAM GOLD DIFF + the team-wide
        # delta. Mirrors what was shipped before; the lane breakdown
        # sits underneath when available.
        row = QHBoxLayout()
        row.setSpacing(styles.SPACING_GRID)

        label = QLabel("TEAM GOLD DIFF")
        label.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            " letter-spacing: 1.2px;"
        )
        row.addWidget(label)

        self._value_label = QLabel("0")
        self._value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._value_label.setStyleSheet(self._value_stylesheet(0))
        row.addWidget(self._value_label, 1)

        outer.addLayout(row)

        # Lane-breakdown rows. Created hidden; surfaced only when the
        # gold-diff service returns a non-empty lane_breakdown (i.e.
        # when the inference heuristic has high enough confidence).
        # Empty/ambiguous → falls back to team-only display silently.
        # Store name+value label pairs together so visibility toggling
        # doesn't need parent-tree walking.
        self._lane_rows: dict[str, tuple[QLabel, QLabel]] = {}
        for lane in LANE_ORDER:
            lane_row = QHBoxLayout()
            lane_row.setSpacing(styles.SPACING_GRID)
            name = QLabel(lane.upper())
            name.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" font-size: {styles.FS_CAPTION}px; font-weight: 700;"
                " letter-spacing: 0.8px;"
            )
            lane_row.addWidget(name)
            value = QLabel("0")
            value.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            value.setStyleSheet(self._lane_stylesheet(0))
            lane_row.addWidget(value, 1)
            self._lane_rows[lane] = (name, value)
            outer.addLayout(lane_row)
        self._set_lanes_visible(False)
        self.hide()

    # -- public API ------------------------------------------------------

    def set_diff(self, gold_diff: dict) -> None:
        """Update the displayed value. Accepts the ``GoldDiff`` shape
        from ``compute_team_gold_diff`` — keys: ``team_blue``,
        ``team_red``, ``lane_breakdown``."""
        team_value = int(gold_diff.get("team_blue", 0))
        self._value_label.setText(_format_gold_delta(team_value))
        self._value_label.setStyleSheet(self._value_stylesheet(team_value))

        lane_breakdown = gold_diff.get("lane_breakdown") or {}
        if not lane_breakdown:
            self._set_lanes_visible(False)
            return
        for lane, (_name, value_label) in self._lane_rows.items():
            v = int(lane_breakdown.get(lane, 0))
            value_label.setText(_format_gold_delta(v))
            value_label.setStyleSheet(self._lane_stylesheet(v))
        self._set_lanes_visible(True)

    def _set_lanes_visible(self, on: bool) -> None:
        for name_label, value_label in self._lane_rows.values():
            name_label.setVisible(on)
            value_label.setVisible(on)

    @staticmethod
    def _value_stylesheet(value: int) -> str:
        return (
            f"color: {_color_for_delta(value)};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_DISPLAY}px;"
            " font-weight: 700;"
        )

    @staticmethod
    def _lane_stylesheet(value: int) -> str:
        """Same color ramp as the team-level value, smaller font for
        the per-lane rows."""
        return (
            f"color: {_color_for_delta(value)};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px;"
            " font-weight: 700;"
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
