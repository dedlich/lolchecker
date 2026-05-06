"""Floating scoreboard widget — kills, gold delta, dragons per team,
plus per-lane matchup gold diff and clickable summoner-spell timers.

Visibility is driven externally via ``set_peek_visible(bool)`` —
typically wired in __main__ to ``state_store.scoreboard_visible``,
which the vision subsystem flips when it detects the in-game TAB
scoreboard on screen, and which the ``toggle_scoreboard`` hotkey
flips manually. We deliberately do NOT poll the keyboard: low-level
key polling (``GetAsyncKeyState`` / ``SetWindowsHookEx``) is the
exact pattern Riot's Vanguard flags as suspicious. See
``tests/lint/test_no_input_hooks.py``.

Data sources (all from LCDA):
  - Per-player kills/items value: aggregated into TeamAggregate
  - Per-lane gold diff: ally(position).items_value - enemy(position).items_value
  - Per-player summoner spells: LivePlayer.spell_one / spell_two
  - Dragon/Baron/Herald counts: derived from event log via KillerName ↔ team

Spell timers persist while the panel is hidden — the user clicks a
spell icon when they see it used, the cooldown ticks down even after
the panel hides, and the next time it peeks shows the current state.
"""
from __future__ import annotations

import time as _time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from ..advisor.decision_engine import win_probability
from ..lcda.players import LivePlayer, TeamAggregate
from ..lcda.source import LcdaSnapshot
from . import styles
from .floating_widget import FloatingWidget

# In-game role labels we show, in display order. LCDA's ``position``
# field uses these tokens for non-fill-rolelocked players.
_ROLES: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
_ROLE_LABEL: dict[str, str] = {
    "TOP": "TOP", "JUNGLE": "JNG", "MIDDLE": "MID",
    "BOTTOM": "BOT", "UTILITY": "SUP",
}


def _fmt_gold(value: int) -> str:
    """e.g. 25978 -> '26.0k'"""
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    if value >= 1000:
        return f"{value / 1000:.2f}k"
    return str(value)


_VALID_POSITIONS: frozenset[str] = frozenset(
    {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
)


def _impute_positions(team: list[LivePlayer]) -> list[LivePlayer]:
    """Fill in missing positions on a 5-player team.

    LCDA reports the active player's ``position`` as ``"NONE"`` (or empty)
    in some queues — custom vs bots, fill role with no autofill assignment,
    or pre-pick states. The other four players on the team always have a
    canonical role, so we can deduce the active player's role from the gap.

    Returns a NEW list of LivePlayer with imputed positions; never mutates
    the input.
    """
    valid = [p for p in team if (p.position or "").upper() in _VALID_POSITIONS]
    invalid = [p for p in team if (p.position or "").upper() not in _VALID_POSITIONS]
    if not invalid or not valid:
        return list(team)
    filled = {(p.position or "").upper() for p in valid}
    missing = [r for r in _VALID_POSITIONS if r not in filled]
    if len(invalid) != len(missing):
        # Multiple unknowns — bail out; per-lane row will show "—".
        return list(team)
    out: list[LivePlayer] = list(valid)
    # Deterministic pairing — order positions canonically and assign 1:1.
    role_order = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
    pending = [r for r in role_order if r in missing]
    for player, role in zip(invalid, pending, strict=False):
        out.append(LivePlayer(
            summoner_name=player.summoner_name,
            champion_name=player.champion_name,
            team=player.team,
            spell_one=player.spell_one,
            spell_two=player.spell_two,
            level=player.level,
            kills=player.kills,
            deaths=player.deaths,
            assists=player.assists,
            creep_score=player.creep_score,
            items_value=player.items_value,
            respawn_timer=player.respawn_timer,
            position=role,
        ))
    return out


def _short_name(player: "LivePlayer | None") -> str:
    """Champion name truncated for the per-lane row.

    Returns ``"—"`` when the lane has no LCDA data (rare — autofill / pre-pick
    states), otherwise the champion's short name capped at 12 chars to keep
    the row width predictable across all 5 lanes."""
    if player is None or not player.champion_name:
        return "—"
    name = str(player.champion_name)
    return name if len(name) <= 12 else name[:11] + "…"


class SpellSlot(QLabel):
    """Clickable summoner-spell icon with a manual cooldown timer.

    Click once → start the cooldown countdown (uses the spell's base
    cooldown from ``SPELL_BASE_COOLDOWN``). Click again while ticking
    → reset to ready. The icon dims and gets a countdown overlay while
    on cooldown; when expired it returns to the ready state.

    The slot keeps its own QTimer so cooldowns continue to tick down
    even when the parent scoreboard is hidden between tab-peeks.
    """

    SIZE_PX = 24

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(self.SIZE_PX, self.SIZE_PX)
        self.setScaledContents(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._spell_name: str = ""
        self._cooldown_s: float = 0.0
        self._started_at: float | None = None
        # 1Hz timer — cheap, only running when a cooldown is active.
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._base_pixmap: QPixmap | None = None
        self._base_style = (
            f"border: 1px solid {styles.TEXT_MUTED};"
            f" border-radius: 3px;"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px;"
            f" font-weight: 800;"
            f" color: {styles.TEXT_PRIMARY};"
        )
        self.setStyleSheet(self._base_style)

    def configure(self, spell_name: str, cooldown_s: float, pixmap: "QPixmap | None") -> None:
        """Set the spell this slot represents. Called per-snapshot."""
        # Reset cooldown when the spell identity changes (e.g. a level-7
        # Smite swap on jungle items would otherwise leave a stale timer).
        if spell_name != self._spell_name:
            self._stop()
        self._spell_name = spell_name
        self._cooldown_s = max(0.0, float(cooldown_s))
        if pixmap is not None and not pixmap.isNull():
            self._base_pixmap = pixmap
            if self._started_at is None:
                self.setPixmap(pixmap)
                self.setText("")
        else:
            self._base_pixmap = None
            if self._started_at is None:
                self.clear()
                # Fall back to a 2-letter glyph so the slot is still tappable.
                self.setText(spell_name[:2].upper() if spell_name else "")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton or not self._spell_name:
            super().mousePressEvent(event)
            return
        if self._started_at is None:
            self._start()
        else:
            self._stop()
        event.accept()

    def _start(self) -> None:
        if self._cooldown_s <= 0:
            return
        self._started_at = _time.monotonic()
        self._tick.start()
        self._render()

    def _stop(self) -> None:
        self._started_at = None
        self._tick.stop()
        self._render()

    def _on_tick(self) -> None:
        if self._started_at is None:
            self._tick.stop()
            return
        elapsed = _time.monotonic() - self._started_at
        if elapsed >= self._cooldown_s:
            self._stop()
            return
        self._render()

    def _render(self) -> None:
        if self._started_at is None:
            # Ready state — full icon, no overlay.
            if self._base_pixmap is not None:
                self.setPixmap(self._base_pixmap)
                self.setText("")
            else:
                self.clear()
                self.setText(self._spell_name[:2].upper() if self._spell_name else "")
            self.setStyleSheet(self._base_style)
            return
        # Cooling — show countdown text on top of a dimmed background.
        elapsed = _time.monotonic() - self._started_at
        remaining = max(0.0, self._cooldown_s - elapsed)
        # Drop the icon, render bold text instead.
        self.clear()
        self.setText(f"{int(remaining)}")
        self.setStyleSheet(
            f"background-color: rgba(20, 20, 20, 200);"
            f" border: 1px solid {styles.WARNING};"
            f" border-radius: 3px;"
            f" font-family: {styles.FONT_MONO};"
            f" font-size: {styles.FS_LABEL}px; font-weight: 800;"
            f" color: {styles.WARNING};"
        )


class ScoreboardWidget(FloatingWidget):
    KEY = "scoreboard"
    DEFAULT_POS = (560, 12)
    DEFAULT_SIZE = (520, 280)

    # Latest snapshot stored so visibility toggles re-render the same data.
    _latest_snapshot: LcdaSnapshot | None = None

    # Lane row icon dimension — readable at a glance during a tab-peek.
    LANE_ICON_PX = 36

    def __init__(self) -> None:
        super().__init__()
        # Champion display name → 32x32 QPixmap, populated externally
        # via ``set_champion_icons``. We rescale to LANE_ICON_PX on use.
        self._champion_icons: dict[str, QPixmap] = {}
        # Summoner-spell-name → 32x32 QPixmap (Flash, Ignite, …).
        self._spell_icons: dict[str, QPixmap] = {}
        self.setStyleSheet(styles.floating_panel_stylesheet())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(4)

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

        # Per-lane matchup gold diff — five rows of "ally  diff  enemy".
        # Stored as a 5x3 grid of labels we mutate in update_snapshot.
        lane_grid = QGridLayout()
        lane_grid.setHorizontalSpacing(6)
        lane_grid.setVerticalSpacing(1)
        lane_grid.setContentsMargins(0, 4, 0, 0)
        # Each row: tag | ally_champ | ally_ss1 | ally_ss2 | diff |
        # enemy_ss1 | enemy_ss2 | enemy_champ
        self._lane_rows: dict[str, dict[str, object]] = {}
        for row, role in enumerate(_ROLES):
            tag = QLabel(_ROLE_LABEL[role])
            tag.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" font-family: {styles.FONT_MONO};"
                f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            )
            tag.setMinimumWidth(34)
            ally = QLabel()
            ally.setFixedSize(self.LANE_ICON_PX, self.LANE_ICON_PX)
            ally.setScaledContents(True)
            ally.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            ally.setStyleSheet(
                f"border: 1px solid {styles.TEAM_ALLY}; border-radius: 3px;"
            )
            ally_ss1 = SpellSlot()
            ally_ss2 = SpellSlot()
            diff = QLabel("·")
            diff.setStyleSheet(
                f"color: {styles.TEXT_MUTED};"
                f" font-family: {styles.FONT_MONO};"
                f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            )
            diff.setAlignment(Qt.AlignmentFlag.AlignCenter)
            diff.setMinimumWidth(70)
            enemy_ss1 = SpellSlot()
            enemy_ss2 = SpellSlot()
            enemy = QLabel()
            enemy.setFixedSize(self.LANE_ICON_PX, self.LANE_ICON_PX)
            enemy.setScaledContents(True)
            enemy.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            enemy.setStyleSheet(
                f"border: 1px solid {styles.TEAM_ENEMY}; border-radius: 3px;"
            )
            lane_grid.addWidget(tag,        row, 0)
            lane_grid.addWidget(ally,       row, 1)
            lane_grid.addWidget(ally_ss1,   row, 2)
            lane_grid.addWidget(ally_ss2,   row, 3)
            lane_grid.addWidget(diff,       row, 4)
            lane_grid.addWidget(enemy_ss1,  row, 5)
            lane_grid.addWidget(enemy_ss2,  row, 6)
            lane_grid.addWidget(enemy,      row, 7)
            self._lane_rows[role] = {
                "ally": ally, "ally_ss1": ally_ss1, "ally_ss2": ally_ss2,
                "diff": diff, "enemy_ss1": enemy_ss1, "enemy_ss2": enemy_ss2,
                "enemy": enemy,
            }
        outer.addLayout(lane_grid)

        # Visibility is driven externally — see ``set_peek_visible``.
        # The vision subsystem detects the in-game scoreboard via screen
        # analysis (no keyboard polling) and pushes ``scoreboard_visible``
        # into the StateStore; __main__ wires that signal through to here.
        self._peek_armed = False

        self.hide()

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None or snapshot.ally_aggregate is None or snapshot.enemy_aggregate is None:
            self._latest_snapshot = None
            self.hide()
            return
        # Cache for tab-toggle re-show even when no new snapshot is arriving.
        self._latest_snapshot = snapshot
        # Visibility is driven by the tab-key poll, not by snapshot arrival.
        # Update text contents regardless so the panel is current the moment
        # the user holds tab.
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

        # Per-lane gold diff — pair players by LCDA position field.
        # LCDA sometimes reports the *active* player's position as "NONE"
        # (custom-vs-bots, fill role, etc.). When that happens we infer
        # their lane from whichever role is missing on their team.
        allies = _impute_positions(list(getattr(snapshot, "allies", []) or []))
        enemies = _impute_positions(list(getattr(snapshot, "enemies", []) or []))
        for role, slots in self._lane_rows.items():
            ally_lbl = slots["ally"]; diff_lbl = slots["diff"]; enemy_lbl = slots["enemy"]
            ally_ss1 = slots["ally_ss1"]; ally_ss2 = slots["ally_ss2"]
            enemy_ss1 = slots["enemy_ss1"]; enemy_ss2 = slots["enemy_ss2"]
            ally_p = next((p for p in allies if (p.position or "").upper() == role), None)
            enemy_p = next((p for p in enemies if (p.position or "").upper() == role), None)
            self._set_champion(ally_lbl, ally_p)
            self._set_champion(enemy_lbl, enemy_p)
            self._configure_spell_slot(ally_ss1, ally_p, "spell_one")
            self._configure_spell_slot(ally_ss2, ally_p, "spell_two")
            self._configure_spell_slot(enemy_ss1, enemy_p, "spell_one")
            self._configure_spell_slot(enemy_ss2, enemy_p, "spell_two")
            if ally_p is None or enemy_p is None:
                diff_lbl.setText("·")
                diff_lbl.setStyleSheet(
                    f"color: {styles.TEXT_MUTED};"
                    f" font-family: {styles.FONT_MONO};"
                    f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
                )
                continue
            d = ally_p.items_value - enemy_p.items_value
            arrow = "▲" if d > 0 else ("▼" if d < 0 else "·")
            color = (
                styles.TEAM_ALLY if d > 0
                else styles.TEAM_ENEMY if d < 0
                else styles.TEXT_MUTED
            )
            diff_lbl.setText(f"{arrow} {_fmt_gold(abs(d))}")
            diff_lbl.setStyleSheet(
                f"color: {color}; font-family: {styles.FONT_MONO};"
                f" font-size: {styles.FS_LABEL}px; font-weight: 700;"
            )

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

    def set_champion_icons(self, icons: dict[str, QPixmap]) -> None:
        """Provide champion-name → QPixmap mapping. Called once at startup
        from __main__ after Data Dragon icons finish prefetching. Any
        already-rendered rows pick up the new icons on the next snapshot."""
        self._champion_icons.update(icons)

    def set_spell_icons(self, icons: dict[str, QPixmap]) -> None:
        """Provide spell-name → QPixmap mapping (Flash, Ignite, …)."""
        self._spell_icons.update(icons)

    def _configure_spell_slot(
        self, slot: SpellSlot, player: "LivePlayer | None", attr: str,
    ) -> None:
        """Push the player's (spell_one|spell_two) into a SpellSlot."""
        if player is None:
            slot.configure("", 0.0, None)
            return
        spell = getattr(player, attr, None)
        if spell is None:
            slot.configure("", 0.0, None)
            return
        slot.configure(
            spell.name or "",
            float(spell.cooldown or 0.0),
            self._spell_icons.get(spell.name or ""),
        )

    def _set_champion(self, label: QLabel, player: "LivePlayer | None") -> None:
        """Render the champion icon (or a textual fallback) into a lane label."""
        if player is None or not player.champion_name:
            label.clear()
            label.setText("—")
            return
        pm = self._champion_icons.get(str(player.champion_name))
        if pm is not None:
            label.setPixmap(pm)
            label.setText("")
            return
        # No icon yet (DataDragon prefetch still in flight) — fall back to
        # the short name so the row is never visually empty.
        label.clear()
        label.setText(_short_name(player))

    def set_peek_visible(self, visible: bool) -> None:
        """External visibility driver. Called by the StateStore subscription
        wired in __main__ — the vision subsystem (or the toggle_scoreboard
        hotkey) flips ``state.scoreboard_visible``, which routes here.

        Snapshot must have arrived at least once for the panel to show;
        an empty panel hovering over an in-game scoreboard would be confusing.
        """
        should_show = visible and self._latest_snapshot is not None
        if should_show and not self.isVisible():
            self.fade_appear()
        elif not should_show and self.isVisible():
            self.hide()

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
