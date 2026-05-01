"""Floating scoreboard widget — kills, gold delta, dragons per team.

Inspired by Blitz's "post-game-style" scoreboard that hovers over the
TAB area in-game. Default position: top-center of the screen, where
the user typically expects this kind of info to live.

Data sources (all from LCDA):
  - Per-player kills/items value: aggregated into TeamAggregate
  - Dragon/Baron/Herald counts: derived from event log via KillerName ↔ team

Gold is approximated by summing item prices — LCDA does not expose
per-player current gold for non-active players. The displayed delta
is "team items value" which closely tracks "team gold spent".
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from ..advisor.decision_engine import win_probability
from ..lcda.players import TeamAggregate
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget


def _fmt_gold(value: int) -> str:
    """e.g. 25978 -> '26.0k'"""
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    if value >= 1000:
        return f"{value / 1000:.2f}k"
    return str(value)


class ScoreboardWidget(FloatingWidget):
    KEY = "scoreboard"
    DEFAULT_POS = (640, 12)
    DEFAULT_SIZE = (340, 64)

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(styles.floating_panel_stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(2)

        # Top row: kills + gold delta + kills (mirrored)
        top = QHBoxLayout()
        top.setSpacing(8)

        self._ally_kills = QLabel("0")
        self._ally_kills.setStyleSheet(
            f"color: {styles.TEAM_ALLY};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_DISPLAY}px; font-weight: 700;"
        )
        self._ally_kills.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._ally_kills)

        self._gold_delta = QLabel("—")
        self._gold_delta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gold_delta.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_HEADING}px; font-weight: 700;"
        )
        # Stable width prevents layout jitter as the lead/deficit value
        # flips arrow direction (▲/▼/·) — different glyph widths would
        # otherwise nudge the kill counters left/right between updates.
        self._gold_delta.setMinimumWidth(180)
        top.addWidget(self._gold_delta, 1)

        self._enemy_kills = QLabel("0")
        self._enemy_kills.setStyleSheet(
            f"color: {styles.TEAM_ENEMY};"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_DISPLAY}px; font-weight: 700;"
        )
        self._enemy_kills.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._enemy_kills)

        outer.addLayout(top)

        # Bottom row: dragons + barons per team
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._ally_objectives = QLabel("")
        self._ally_objectives.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_LABEL}px;"
        )
        self._ally_objectives.setAlignment(Qt.AlignmentFlag.AlignLeft)
        bottom.addWidget(self._ally_objectives, 1)

        self._game_time = QLabel("")
        self._game_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._game_time.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            f" font-family: {styles.FONT_MONO}; font-size: {styles.FS_LABEL}px;"
        )
        self._game_time.setTextFormat(Qt.TextFormat.RichText)
        bottom.addWidget(self._game_time)

        self._enemy_objectives = QLabel("")
        self._enemy_objectives.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: {styles.FS_LABEL}px;"
        )
        self._enemy_objectives.setAlignment(Qt.AlignmentFlag.AlignRight)
        bottom.addWidget(self._enemy_objectives, 1)

        outer.addLayout(bottom)
        self.hide()

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None or snapshot.ally_aggregate is None or snapshot.enemy_aggregate is None:
            self.hide()
            return
        self.fade_appear()
        ally = snapshot.ally_aggregate
        enemy = snapshot.enemy_aggregate

        self._ally_kills.setText(str(ally.kills))
        self._enemy_kills.setText(str(enemy.kills))

        delta = ally.items_value - enemy.items_value
        # Directional arrow + color makes the lead/deficit read at a glance.
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "·")
        color = (
            styles.TEAM_ALLY if delta > 0
            else styles.TEAM_ENEMY if delta < 0
            else styles.TEAM_NEUTRAL
        )
        muted = styles.TEXT_MUTED
        self._gold_delta.setText(
            f"<span style='color:{muted}'>{_fmt_gold(ally.items_value)}</span>"
            f"  <span style='color:{color}; font-weight:800'>"
            f"{arrow} {_fmt_gold(abs(delta))}</span>"
            f"  <span style='color:{muted}'>{_fmt_gold(enemy.items_value)}</span>"
        )

        self._ally_objectives.setText(self._objectives_line(ally))
        self._enemy_objectives.setText(self._objectives_line(enemy))

        gt = snapshot.game_time
        mm, ss = divmod(int(gt), 60)
        win_pct = int(win_probability(snapshot) * 100)
        if win_pct >= 60:
            pct_color = styles.SUCCESS
        elif win_pct <= 40:
            pct_color = styles.DANGER
        else:
            pct_color = styles.TEXT_MUTED
        muted = styles.TEXT_MUTED
        self._game_time.setText(
            f"<span style='color:{muted}'>{mm:d}:{ss:02d}</span>"
            f"  <span style='color:{muted}'>·</span>"
            f"  <span style='color:{pct_color}'>{win_pct}%</span>"
        )

    @staticmethod
    def _objectives_line(agg: TeamAggregate) -> str:
        bits = []
        if agg.dragons:
            bits.append(f"🐉 {agg.dragons}")
        if agg.barons:
            bits.append(f"👑 {agg.barons}")
        if agg.heralds:
            bits.append(f"👁 {agg.heralds}")
        return "  ".join(bits)
