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

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QFrame, QLabel, QMainWindow, QVBoxLayout, QWidget

from . import styles
from .enemy_row import EnemyRow
from .pick_card import PickCard
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
        frameless: bool = False,
        always_on_top: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Champ Select Assistant")
        self.resize(420, 640)

        flags = self.windowFlags()
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(
            styles.SPACING_GRID,
            styles.SPACING_GRID,
            styles.SPACING_GRID,
            styles.SPACING_GRID,
        )
        layout.setSpacing(styles.SPACING_GRID)

        title = QLabel("Champ Select Assistant")
        title.setObjectName("title")
        layout.addWidget(title)

        # Enemy team section
        enemy_panel = QFrame()
        enemy_panel.setProperty("panel", True)
        enemy_layout = QVBoxLayout(enemy_panel)
        enemy_layout.setContentsMargins(8, 8, 8, 8)
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

        layout.addWidget(enemy_panel)

        # Picks section
        picks_panel = QFrame()
        picks_panel.setProperty("panel", True)
        picks_outer = QVBoxLayout(picks_panel)
        picks_outer.setContentsMargins(8, 8, 8, 8)
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

        layout.addWidget(picks_panel)
        layout.addStretch(1)

        self._status_bar = ConnectionStatusBar()
        self.setStatusBar(self._status_bar)

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
