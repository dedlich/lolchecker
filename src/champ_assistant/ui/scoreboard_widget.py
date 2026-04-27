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
        self.setStyleSheet(
            f"QFrame[panel='true'] {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f"  stop:0 rgba(20, 26, 34, 180), stop:1 rgba(10, 14, 20, 180));"
            f" border: 1px solid rgba(60, 70, 85, 200);"
            f" border-radius: {styles.RADIUS}px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(2)

        # Top row: kills + gold delta + kills (mirrored)
        top = QHBoxLayout()
        top.setSpacing(8)

        self._ally_kills = QLabel("0")
        self._ally_kills.setStyleSheet(
            "color: #6BBBFF; font-family: SF Mono, Consolas, monospace;"
            " font-size: 18px; font-weight: 700;"
        )
        self._ally_kills.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._ally_kills)

        self._gold_delta = QLabel("—")
        self._gold_delta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._gold_delta.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY};"
            " font-family: SF Mono, Consolas, monospace;"
            " font-size: 14px; font-weight: 700;"
        )
        top.addWidget(self._gold_delta, 1)

        self._enemy_kills = QLabel("0")
        self._enemy_kills.setStyleSheet(
            "color: #FF6B6B; font-family: SF Mono, Consolas, monospace;"
            " font-size: 18px; font-weight: 700;"
        )
        self._enemy_kills.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self._enemy_kills)

        outer.addLayout(top)

        # Bottom row: dragons + barons per team
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._ally_objectives = QLabel("")
        self._ally_objectives.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: 11px;"
        )
        self._ally_objectives.setAlignment(Qt.AlignmentFlag.AlignLeft)
        bottom.addWidget(self._ally_objectives, 1)

        self._game_time = QLabel("")
        self._game_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._game_time.setStyleSheet(
            f"color: {styles.TEXT_MUTED};"
            " font-family: SF Mono, Consolas, monospace; font-size: 11px;"
        )
        bottom.addWidget(self._game_time)

        self._enemy_objectives = QLabel("")
        self._enemy_objectives.setStyleSheet(
            f"color: {styles.TEXT_SECONDARY}; font-size: 11px;"
        )
        self._enemy_objectives.setAlignment(Qt.AlignmentFlag.AlignRight)
        bottom.addWidget(self._enemy_objectives, 1)

        outer.addLayout(bottom)
        self.hide()

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None or snapshot.ally_aggregate is None or snapshot.enemy_aggregate is None:
            self.hide()
            return
        self.show()
        ally = snapshot.ally_aggregate
        enemy = snapshot.enemy_aggregate

        self._ally_kills.setText(str(ally.kills))
        self._enemy_kills.setText(str(enemy.kills))

        delta = ally.items_value - enemy.items_value
        # Directional arrow + color makes the lead/deficit read at a glance.
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "·")
        color = "#6BBBFF" if delta > 0 else ("#FF6B6B" if delta < 0 else "#888")
        self._gold_delta.setText(
            f"<span style='color:#888'>{_fmt_gold(ally.items_value)}</span>"
            f"  <span style='color:{color}; font-weight:800'>"
            f"{arrow} {_fmt_gold(abs(delta))}</span>"
            f"  <span style='color:#888'>{_fmt_gold(enemy.items_value)}</span>"
        )

        self._ally_objectives.setText(self._objectives_line(ally))
        self._enemy_objectives.setText(self._objectives_line(enemy))

        gt = snapshot.game_time
        mm, ss = divmod(int(gt), 60)
        self._game_time.setText(f"{mm:d}:{ss:02d}")

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
