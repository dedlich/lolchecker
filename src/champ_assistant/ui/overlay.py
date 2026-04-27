"""Main overlay window.

Layout (masterplan §2):
  ┌────────────────────────┐
  │  Champ Select Assistant │  ← title
  ├────────────────────────┤
  │  ENEMY TEAM             │
  │  [enemy row × 5]        │
  ├────────────────────────┤
  │  YOUR PICKS             │
  │  [pick card × N]        │
  ├────────────────────────┤
  │  Connected              │  ← status bar
  └────────────────────────┘

Frameless + always-on-top behind a flag (off by default → tests render
in a normal window which is friendlier to headless / pytest-qt).
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QFrame, QLabel, QMainWindow, QVBoxLayout, QWidget

from .. import overlay_config
from ..lcda.source import LcdaSnapshot
from . import styles
from .enemy_row import EnemyRow
from .objective_panel import ObjectivePanel
from .pick_card import PickCard
from .power_spike_panel import PowerSpikePanel
from .summoner_tracker import SummonerTrackerPanel
from .title_bar import TitleBar
from .view_model import SessionView
from .widgets import ConnectionStatusBar


class MainOverlay(QMainWindow):
    HOTKEY_HIDE = "Ctrl+H"
    HOTKEY_REFRESH = "Ctrl+R"

    refresh_requested = pyqtSignal()
    enemy_role_clicked = pyqtSignal(int)  # cell_id of the clicked enemy slot

    def __init__(
        self,
        *,
        frameless: bool | None = None,
        always_on_top: bool | None = None,
        parent: QWidget | None = None,
        load_persisted_state: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Champ Assistant")
        self._persisted = (
            overlay_config.load() if load_persisted_state else overlay_config.OverlayState()
        )
        # Explicit kwargs win over persisted state — preserves test setups
        # that build the overlay without flags.
        if frameless is None:
            frameless = self._persisted.frameless if load_persisted_state else False
        if always_on_top is None:
            always_on_top = (
                self._persisted.always_on_top if load_persisted_state else False
            )
        self._is_frameless = frameless
        self._save_state = load_persisted_state
        self.resize(self._persisted.width, self._persisted.height)

        flags = self.windowFlags()
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
            # Tool gives us a slim, no-taskbar window that loses focus quickly
            # back to the game when the user clicks elsewhere.
            flags |= Qt.WindowType.Tool
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Custom title bar (drag + minimize + close) — only meaningful when
        # frameless. Always built so the layout stays uniform and tests pass.
        self._title_bar = TitleBar()
        self._title_bar.set_title("Champ Assistant")
        self._title_bar.drag_delta.connect(self._on_title_drag)
        self._title_bar.minimize_clicked.connect(self._toggle_collapsed)
        self._title_bar.close_clicked.connect(self.close)
        if not frameless:
            self._title_bar.hide()
        outer.addWidget(self._title_bar)

        # Body container (everything below the title bar)
        self._body = QWidget()
        outer.addWidget(self._body, 1)

        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(
            styles.SPACING_GRID,
            styles.SPACING_GRID,
            styles.SPACING_GRID,
            styles.SPACING_GRID,
        )
        body_layout.setSpacing(styles.SPACING_GRID)

        # Enemy team section (shown only during champ-select)
        self._enemy_panel = QFrame()
        self._enemy_panel.setProperty("panel", True)
        enemy_layout = QVBoxLayout(self._enemy_panel)
        enemy_layout.setContentsMargins(10, 10, 10, 10)
        enemy_layout.setSpacing(4)

        enemy_title = QLabel("Enemy Team")
        enemy_title.setObjectName("sectionTitle")
        enemy_layout.addWidget(enemy_title)

        self._enemy_rows: list[EnemyRow] = []
        for _ in range(5):
            row = EnemyRow()
            row.role_clicked.connect(self.enemy_role_clicked.emit)
            self._enemy_rows.append(row)
            enemy_layout.addWidget(row)

        body_layout.addWidget(self._enemy_panel)

        # Picks section (champ-select)
        self._picks_panel = QFrame()
        self._picks_panel.setProperty("panel", True)
        picks_outer = QVBoxLayout(self._picks_panel)
        picks_outer.setContentsMargins(10, 10, 10, 10)
        picks_outer.setSpacing(4)

        picks_title = QLabel("Your Picks")
        picks_title.setObjectName("sectionTitle")
        picks_outer.addWidget(picks_title)

        self._picks_container = QVBoxLayout()
        self._picks_container.setSpacing(4)
        picks_outer.addLayout(self._picks_container)

        self._no_picks_label = QLabel("(no suggestions yet)")
        self._no_picks_label.setProperty("role", "muted")
        self._no_picks_label.setStyleSheet(f"color: {styles.TEXT_MUTED};")
        picks_outer.addWidget(self._no_picks_label)

        body_layout.addWidget(self._picks_panel)

        self._power_spike_panel = PowerSpikePanel()
        body_layout.addWidget(self._power_spike_panel)

        self._objective_panel = ObjectivePanel()
        body_layout.addWidget(self._objective_panel)

        self._summoner_tracker = SummonerTrackerPanel()
        body_layout.addWidget(self._summoner_tracker)

        body_layout.addStretch(1)

        self._status_bar = ConnectionStatusBar()
        self.setStatusBar(self._status_bar)

        # Apply persisted position once the window has been polished.
        if load_persisted_state and self._persisted.x is not None:
            self._restore_position(self._persisted)
        elif load_persisted_state:
            self._anchor_to_screen_edge(self._persisted.anchor)

        if load_persisted_state and self._persisted.collapsed:
            self._set_body_visible(False)

        # Hotkeys (kept as instance attrs so tests can introspect / fire them)
        self._hide_shortcut = QShortcut(QKeySequence(self.HOTKEY_HIDE), self)
        self._hide_shortcut.activated.connect(self.hide)
        self._refresh_shortcut = QShortcut(QKeySequence(self.HOTKEY_REFRESH), self)
        self._refresh_shortcut.activated.connect(self.refresh_requested.emit)

        self.setStyleSheet(styles.global_stylesheet())

        # Champion icon cache (string key like "Garen" → scaled QPixmap).
        # Filled asynchronously by the icon-prefetch task in __main__.
        self._champion_icons: dict[str, QPixmap] = {}
        self._last_view: SessionView | None = None

    @property
    def status_bar(self) -> ConnectionStatusBar:
        return self._status_bar

    @property
    def objective_panel(self) -> ObjectivePanel:
        return self._objective_panel

    @property
    def summoner_tracker(self) -> SummonerTrackerPanel:
        return self._summoner_tracker

    @property
    def enemy_rows(self) -> list[EnemyRow]:
        return list(self._enemy_rows)

    def set_champion_icons(self, icons: dict[str, QPixmap]) -> None:
        """Inject prefetched champion icons (key → scaled QPixmap).

        Called by the icon-prefetch task once it finishes. If a session view
        was already rendered, re-render it so the icons appear immediately.
        """
        self._champion_icons.update(icons)
        if self._last_view is not None:
            self.update_view(self._last_view)

    def update_view(self, view: SessionView) -> None:
        self._last_view = view
        self._status_bar.set_state(view.connection_state)
        self._update_enemies(view)
        self._update_picks(view)

    def _icon_for_key(self, key: str | None) -> QPixmap | None:
        if not key:
            return None
        return self._champion_icons.get(key)

    def _update_enemies(self, view: SessionView) -> None:
        their_team = view.session.their_team if view.session else []
        for i, row in enumerate(self._enemy_rows):
            if i < len(their_team):
                member = their_team[i]
                name = view.enemy_names.get(member.champion_id) if member.champion_id else None
                key = view.enemy_keys.get(member.champion_id) if member.champion_id else None
                counters = view.enemy_counters.get(member.cell_id, [])
                resolved_role = view.enemy_roles.get(member.cell_id, "")
                role_overridden = member.cell_id in view.enemy_role_overridden
                row.set_data(
                    member, name, counters,
                    icon=self._icon_for_key(key),
                    resolved_role=resolved_role,
                    role_overridden=role_overridden,
                )
            else:
                row.clear()

    def _update_picks(self, view: SessionView) -> None:
        # Clear existing cards.
        while self._picks_container.count():
            item = self._picks_container.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if not view.suggestions:
            self._no_picks_label.show()
            return

        self._no_picks_label.hide()
        for s in view.suggestions:
            icon = self._icon_for_key(s.champion_key)
            build = view.suggestion_builds.get(s.champion_key)
            self._picks_container.addWidget(PickCard(s, icon=icon, build=build))

    # -- in-game panels visibility ---------------------------------------

    def update_lcda_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        """Forward LCDA ticks to in-game panels."""
        self._objective_panel.update_snapshot(snapshot)
        self._summoner_tracker.update_snapshot(snapshot)
        self._power_spike_panel.update_snapshot(snapshot)
        # Auto-collapse champ-select sections during a real game.
        self.set_phase_visibility(
            in_champ_select=False,
            in_game=snapshot is not None,
        )

    @property
    def power_spike_panel(self) -> PowerSpikePanel:
        return self._power_spike_panel

    def set_phase_visibility(
        self, *, in_champ_select: bool, in_game: bool
    ) -> None:
        """Show/hide major sections based on the live game phase.

        Champ-select panels collapse during a live game so the overlay
        stays compact. The objective and summoner panels manage their
        own visibility from LCDA snapshots.
        """
        self._enemy_panel.setVisible(in_champ_select or not in_game)
        self._picks_panel.setVisible(in_champ_select or not in_game)

    # -- frameless drag + persistence ------------------------------------

    def _on_title_drag(self, delta: QPoint) -> None:
        self.move(self.pos() + delta)

    def _toggle_collapsed(self) -> None:
        new_visible = not self._body.isVisible()
        self._set_body_visible(new_visible)
        if self._save_state:
            self._persisted.collapsed = not new_visible
            overlay_config.save(self._persisted)

    def _set_body_visible(self, visible: bool) -> None:
        self._body.setVisible(visible)
        # When collapsing, shrink the window to just the title bar height
        if not visible:
            self.resize(self.width(), TitleBar.HEIGHT + self.statusBar().height())
        else:
            self.resize(self.width(), self._persisted.height)

    def _restore_position(self, state: overlay_config.OverlayState) -> None:
        if state.x is None or state.y is None:
            return
        # Make sure the saved position is still on a connected screen,
        # otherwise the overlay would render off-screen.
        screens = QGuiApplication.screens()
        for screen in screens:
            geo = screen.availableGeometry()
            if (
                geo.left() <= state.x <= geo.right() - 50
                and geo.top() <= state.y <= geo.bottom() - 50
            ):
                self.move(state.x, state.y)
                return
        # Fall back to anchored placement when the saved screen is gone.
        self._anchor_to_screen_edge(state.anchor)

    def _anchor_to_screen_edge(self, anchor: str) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 16
        if anchor == "left":
            self.move(geo.left() + margin, geo.top() + margin)
        else:  # right (default)
            self.move(geo.right() - self.width() - margin, geo.top() + margin)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._save_state:
            self._persisted.x = self.x()
            self._persisted.y = self.y()
            self._persisted.width = self.width()
            self._persisted.height = self.height() if self._body.isVisible() else self._persisted.height
            self._persisted.collapsed = not self._body.isVisible()
            overlay_config.save(self._persisted)
        super().closeEvent(event)
