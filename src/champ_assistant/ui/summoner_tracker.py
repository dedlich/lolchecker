"""Live-game enemy summoner spell tracker.

For each enemy, two clickable spell badges. Left-click marks the spell as
just used and starts a cooldown timer. Right-click clears it. The widget
re-renders on every LCDA snapshot so the timer counts down with game time
(Riot's authoritative clock — no local drift).

Visual states:
    Ready      bright icon, no timer
    On CD      dimmed icon + colored M:SS overlay
    Soon-up    timer text turns green when < 20% of cooldown remains
"""
from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from ..lcda.players import LivePlayer
from ..lcda.source import LcdaSnapshot
from ..lcda.spell_tracker import SpellCooldown, SpellTracker
from . import styles


def _fmt_seconds(seconds: float) -> str:
    if seconds <= 0:
        return ""
    total = int(seconds + 0.5)
    minutes, sec = divmod(total, 60)
    if minutes:
        return f"{minutes:d}:{sec:02d}"
    return f"{sec:d}s"


class SpellBadge(QFrame):
    """A 36x36 spell icon that toggles a cooldown on click."""

    SIZE = 36
    clicked = pyqtSignal()
    right_clicked = pyqtSignal()

    def __init__(self, spell_name: str) -> None:
        super().__init__()
        self.spell_name = spell_name
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setProperty("card", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{spell_name} — Klick: Cooldown starten · Rechts: zurücksetzen")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)

        self._icon_label = QLabel()
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setScaledContents(True)
        layout.addWidget(self._icon_label)

        # Translucent overlay text for the timer
        self._timer_label = QLabel(self)
        self._timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer_label.setProperty("role", "timer-small")
        self._timer_label.setStyleSheet(
            f"background-color: rgba(0, 0, 0, 180); color: {styles.TEXT_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
        )
        self._timer_label.hide()

        self._opacity = QGraphicsOpacityEffect(self._icon_label)
        self._opacity.setOpacity(1.0)
        self._icon_label.setGraphicsEffect(self._opacity)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._timer_label.setGeometry(0, 0, self.width(), self.height())

    def set_icon(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._icon_label.setText(self.spell_name[:2].upper())
            self._icon_label.setStyleSheet(
                f"color: {styles.TEXT_SECONDARY}; font-weight: 700;"
            )
            return
        self._icon_label.setPixmap(pixmap)

    def set_cooldown_state(
        self,
        remaining: float,
        cooldown: float,
    ) -> None:
        if remaining <= 0 or cooldown <= 0:
            self._timer_label.hide()
            self._opacity.setOpacity(1.0)
            return
        fraction = remaining / cooldown if cooldown > 0 else 0.0
        color = styles.cooldown_color(fraction)
        self._timer_label.setText(_fmt_seconds(remaining))
        self._timer_label.setStyleSheet(
            f"background-color: rgba(0, 0, 0, 180); color: {color};"
            f" border-radius: {styles.RADIUS_SMALL}px; font-weight: 700;"
        )
        self._timer_label.show()
        self._opacity.setOpacity(0.45)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit()
        else:
            super().mousePressEvent(event)


class EnemyTrackerRow(QFrame):
    """One enemy: champion portrait + name + two spell badges."""

    spell_clicked = pyqtSignal(str, str)        # summoner_name, spell_name
    spell_right_clicked = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "row")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(12)

        self._portrait = QLabel()
        self._portrait.setFixedSize(32, 32)
        self._portrait.setScaledContents(True)
        self._portrait.setStyleSheet(
            f"background-color: {styles.BG_PRIMARY};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" border: 1px solid {styles.BORDER_FAINT};"
        )
        layout.addWidget(self._portrait)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)
        self._champion = QLabel("")
        self._champion.setStyleSheet(
            f"font-weight: 700; font-size: {styles.FS_BODY}px;"
            f" color: {styles.TEXT_PRIMARY};"
        )
        text_col.addWidget(self._champion)
        self._summoner = QLabel("")
        self._summoner.setProperty("role", "muted")
        self._summoner.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
        )
        text_col.addWidget(self._summoner)
        layout.addLayout(text_col, 1)

        self._badge_one = SpellBadge("Flash")
        self._badge_two = SpellBadge("Ignite")
        layout.addWidget(self._badge_one)
        layout.addWidget(self._badge_two)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._summoner_name = ""
        self._wire(self._badge_one)
        self._wire(self._badge_two)

    def _wire(self, badge: SpellBadge) -> None:
        badge.clicked.connect(
            lambda b=badge: self.spell_clicked.emit(self._summoner_name, b.spell_name)
        )
        badge.right_clicked.connect(
            lambda b=badge: self.spell_right_clicked.emit(self._summoner_name, b.spell_name)
        )

    def set_player(
        self,
        player: LivePlayer,
        *,
        portrait: QPixmap | None,
        spell_icons: dict[str, QPixmap],
    ) -> None:
        self._summoner_name = player.summoner_name
        self._champion.setText(player.champion_name or "Unknown")
        self._summoner.setText(player.summoner_name or "—")
        if portrait is not None:
            self._portrait.setPixmap(portrait)
        else:
            self._portrait.clear()
        self._badge_one.spell_name = player.spell_one.name or "?"
        self._badge_two.spell_name = player.spell_two.name or "?"
        self._badge_one.setToolTip(
            f"{self._badge_one.spell_name} — Klick: Cooldown starten"
        )
        self._badge_two.setToolTip(
            f"{self._badge_two.spell_name} — Klick: Cooldown starten"
        )
        self._badge_one.set_icon(spell_icons.get(self._badge_one.spell_name))
        self._badge_two.set_icon(spell_icons.get(self._badge_two.spell_name))

    def set_cooldowns(
        self,
        spell_one: SpellCooldown | None,
        spell_two: SpellCooldown | None,
        game_time: float,
    ) -> None:
        self._apply(self._badge_one, spell_one, game_time)
        self._apply(self._badge_two, spell_two, game_time)

    @staticmethod
    def _apply(
        badge: SpellBadge,
        cd: SpellCooldown | None,
        game_time: float,
    ) -> None:
        if cd is None:
            badge.set_cooldown_state(0.0, 0.0)
            return
        badge.set_cooldown_state(cd.remaining(game_time), cd.cooldown)

    def clear_player(self) -> None:
        self._summoner_name = ""
        self._champion.setText("")
        self._summoner.setText("")
        self._portrait.clear()
        self._badge_one.set_icon(None)
        self._badge_two.set_icon(None)
        self._badge_one.set_cooldown_state(0.0, 0.0)
        self._badge_two.set_cooldown_state(0.0, 0.0)


class SummonerTrackerPanel(QFrame):
    """Live enemy summoner-spell tracker. Hidden out of game."""

    MAX_ENEMIES = 5

    def __init__(self, tracker: SpellTracker | None = None) -> None:
        super().__init__()
        self.setProperty("panel", True)
        self.setObjectName("summonerTrackerPanel")

        # ``tracker or SpellTracker()`` would silently create a phantom
        # tracker when the caller passed an EMPTY SpellTracker — empty
        # tracker's __len__ is 0, which is falsy, so `or` falls through
        # to the new instance. Use explicit None-check.
        self._tracker = tracker if tracker is not None else SpellTracker()
        self._spell_icons: dict[str, QPixmap] = {}
        self._champion_icons: dict[str, QPixmap] = {}
        self._latest_game_time: float = 0.0
        self._latest_enemies: list[LivePlayer] = []
        # (summoner_name, spell_name) -> last accepted-click monotonic ts.
        # Used by the duplicate-click guard in _on_spell_clicked.
        self._last_click_at: dict[tuple[str, str], float] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Live Game — Summoners")
        title.setObjectName("sectionTitle")
        header.addWidget(title, 1)
        self._hint = QLabel("Klick = Cooldown starten · Rechts = reset")
        self._hint.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_CAPTION}px;"
        )
        header.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(header)

        self._rows: list[EnemyTrackerRow] = []
        for _ in range(self.MAX_ENEMIES):
            row = EnemyTrackerRow()
            row.spell_clicked.connect(self._on_spell_clicked)
            row.spell_right_clicked.connect(self._on_spell_right_clicked)
            self._rows.append(row)
            outer.addWidget(row)

        self.hide()

    # -- public API -------------------------------------------------------

    def set_spell_icons(self, icons: dict[str, QPixmap]) -> None:
        self._spell_icons.update(icons)
        self._render()

    def set_champion_icons(self, icons: dict[str, QPixmap]) -> None:
        self._champion_icons.update(icons)
        self._render()

    def update_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        if snapshot is None:
            self.hide()
            return
        self.show()
        self._latest_game_time = snapshot.game_time
        self._latest_enemies = list(snapshot.enemies)
        # Drop entries that have ticked down to zero so the icon brightens.
        self._tracker.gc(snapshot.game_time)
        self._render()

    def tracker(self) -> SpellTracker:
        return self._tracker

    # -- internals --------------------------------------------------------

    def _render(self) -> None:
        for i, row in enumerate(self._rows):
            if i >= len(self._latest_enemies):
                row.clear_player()
                row.hide()
                continue
            row.show()
            player = self._latest_enemies[i]
            portrait = self._champion_icons.get(player.champion_name)
            row.set_player(
                player,
                portrait=portrait,
                spell_icons=self._spell_icons,
            )
            row.set_cooldowns(
                self._tracker.get(player.summoner_name, player.spell_one.name),
                self._tracker.get(player.summoner_name, player.spell_two.name),
                self._latest_game_time,
            )

    # Min interval between two consecutive starts on the same
    # (summoner, spell) pair — protects against accidental double-
    # click. Real cooldowns are 90s+, so a 1s lockout is invisible.
    MIN_TIMER_INTERVAL_S = 1.0

    def _on_spell_clicked(self, summoner_name: str, spell_name: str) -> None:
        if not summoner_name or not spell_name or spell_name == "?":
            return

        # Focus guard: ignore clicks if the application isn't active.
        # Prevents accidental triggers when League has focus + a stray
        # click bubble somehow reaches our overlay (rare, but clean
        # to defend against).
        if not self._app_is_active():
            return

        # Duplicate-click protection: same (summoner, spell) inside
        # MIN_TIMER_INTERVAL_S is treated as a no-op.
        from time import monotonic as _mono
        key = (summoner_name, spell_name)
        last_click = self._last_click_at.get(key)
        now = _mono()
        if last_click is not None and (now - last_click) < self.MIN_TIMER_INTERVAL_S:
            return
        self._last_click_at[key] = now

        cooldown = self._lookup_cooldown(summoner_name, spell_name)
        if cooldown <= 0:
            return
        self._tracker.mark_used(
            summoner_name, spell_name, cooldown, self._latest_game_time
        )
        # Telemetry — emit once per accepted click, never per render.
        try:
            from .. import telemetry as _t
            _t.recorder().record(
                _t.EV_SPELL_TIMER_STARTED,
                {
                    "player_id": summoner_name,
                    "spell": spell_name,
                    "source": "scoreboard_click",
                },
            )
        except Exception:  # noqa: BLE001 — telemetry must never break UI
            pass
        self._render()

    def _on_spell_right_clicked(self, summoner_name: str, spell_name: str) -> None:
        if not summoner_name or not spell_name:
            return
        if not self._app_is_active():
            return
        self._tracker.reset(summoner_name, spell_name)
        try:
            from .. import telemetry as _t
            _t.recorder().record(
                _t.EV_SPELL_TIMER_RESET,
                {
                    "player_id": summoner_name,
                    "spell": spell_name,
                    "source": "scoreboard_click",
                },
            )
        except Exception:  # noqa: BLE001
            pass
        self._render()

    @staticmethod
    def _app_is_active() -> bool:
        """True iff our QApplication has the foreground focus. Returns
        True when the QApplication isn't constructed (defensive — used
        in tests where there's no real app state)."""
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return True
        return app.applicationState() == _Qt.ApplicationState.ApplicationActive

    def _lookup_cooldown(self, summoner_name: str, spell_name: str) -> float:
        for player in self._latest_enemies:
            if player.summoner_name != summoner_name:
                continue
            for spell in (player.spell_one, player.spell_two):
                if spell.name == spell_name:
                    return spell.cooldown
        return 0.0


# Optional callable hook used by tests / debug consumers
SnapshotConsumer = Callable[[LcdaSnapshot | None], None]
