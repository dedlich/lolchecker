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
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from .. import overlay_config
from ..lcda.source import LcdaSnapshot
from . import styles
from .enemy_row import EnemyRow
from .ban_panel import BanPanel
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
    settings_changed = pyqtSignal()       # user saved a new API key
    apply_build_requested = pyqtSignal(str, "PyQt_PyObject", "PyQt_PyObject")
    # (champion_key, rune_names, item_names)
    pick_hover_requested = pyqtSignal(str)  # bubbled from PickCard
    ban_hover_requested = pyqtSignal(str)   # bubbled from BanPanel

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
        # Clamp the persisted height to the available screen height so the
        # overlay never sticks past the bottom of the monitor (the bug from
        # the 1366x768 laptop case).
        target_w, target_h = self._clamp_to_screen(
            self._persisted.width, self._persisted.height
        )
        self.resize(target_w, target_h)

        flags = self.windowFlags()
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        # Always_on_top is no longer set here at construction time — it's
        # applied dynamically when LCDA reports an in-game session
        # (overlay mode). Champ-select stays as a normal window so users
        # can Alt+Tab between LeagueClient and the assistant freely.
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        # Channel state for the overlay/champselect mode switcher.
        self._current_mode: str = "champselect"

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Custom title bar (drag + minimize + close) — only meaningful when
        # frameless. Always built so the layout stays uniform and tests pass.
        from .. import __version__

        self._title_bar = TitleBar()
        self._title_bar.set_title("Champ Assistant")
        self._title_bar.set_version(__version__)
        self._title_bar.drag_delta.connect(self._on_title_drag)
        self._title_bar.minimize_clicked.connect(self._toggle_collapsed)
        self._title_bar.close_clicked.connect(self.close)
        self._title_bar.settings_clicked.connect(self._open_settings)
        self._title_bar.opacity_changed.connect(self._on_opacity_changed)
        self._title_bar.panel_toggled.connect(self._on_panel_toggled)
        self._title_bar.passthrough_toggled.connect(self._on_passthrough_toggled)
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

        # First-launch onboarding banner — created hidden, shown only on
        # first run via show_onboarding_if_needed(). Sits at the top of
        # the body so it's the first thing the user sees.
        from .onboarding import OnboardingBanner
        from .. import overlay_config as _ovc
        def _on_onboarding_dismissed() -> None:
            state = _ovc.load()
            state.onboarding_seen = True
            _ovc.save(state)
        self._onboarding = OnboardingBanner(_on_onboarding_dismissed, parent=self._body)
        body_layout.addWidget(self._onboarding)

        # Champ-select section: enemy + picks side-by-side. The container
        # holds them in a horizontal layout so users get more horizontal
        # breathing room for the long rune/item lines on the pick cards.
        self._champselect_row = QFrame()
        self._champselect_row.setStyleSheet("background: transparent;")
        cs_layout = QHBoxLayout(self._champselect_row)
        cs_layout.setContentsMargins(0, 0, 0, 0)
        cs_layout.setSpacing(styles.SPACING_GRID)

        # Enemy team panel (left column)
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

        cs_layout.addWidget(self._enemy_panel, 1)

        # Picks panel (right column)
        self._picks_panel = QFrame()
        self._picks_panel.setProperty("panel", True)
        picks_outer = QVBoxLayout(self._picks_panel)
        picks_outer.setContentsMargins(10, 10, 10, 10)
        picks_outer.setSpacing(4)

        picks_title = QLabel("Your Picks")
        picks_title.setObjectName("sectionTitle")
        picks_outer.addWidget(picks_title)

        self._picks_container = QVBoxLayout()
        self._picks_container.setSpacing(6)
        picks_outer.addLayout(self._picks_container)

        self._no_picks_label = QLabel(
            "Pick suggestions appear once enemies start locking in."
        )
        self._no_picks_label.setProperty("role", "muted")
        self._no_picks_label.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            f" padding: 8px 4px; font-style: italic;"
        )
        self._no_picks_label.setWordWrap(True)
        picks_outer.addWidget(self._no_picks_label)

        cs_layout.addWidget(self._picks_panel, 1)
        body_layout.addWidget(self._champselect_row)

        # Ban suggestions sit below the side-by-side champ-select row at
        # full width so the rows have room to breathe.
        self._ban_panel = BanPanel()
        self._ban_panel.ban_hover_requested.connect(self.ban_hover_requested.emit)
        body_layout.addWidget(self._ban_panel)

        self._power_spike_panel = PowerSpikePanel()
        body_layout.addWidget(self._power_spike_panel)

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

        # Sync persisted opacity + per-panel toggle states into the title-bar
        # buttons. The panels themselves stay hidden until LCDA delivers a
        # snapshot — _panel_allowed() then gates LCDA forwarding so a
        # toggled-off panel never gets a show() call.
        if load_persisted_state:
            self._title_bar.set_opacity(self._persisted.opacity)
            self._title_bar.set_panel_visible(
                "summoners", self._persisted.show_summoners
            )
            self._title_bar.set_panel_visible(
                "spikes", self._persisted.show_spikes
            )

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

    def show_onboarding_if_needed(self) -> None:
        """Surface the welcome banner only on the user's very first run.
        Read from overlay_config so the decision lives next to the rest
        of the persisted UI state."""
        from .. import overlay_config as _ovc
        state = _ovc.load()
        self._onboarding.maybe_show(already_seen=state.onboarding_seen)

    def update_view(self, view: SessionView) -> None:
        self._last_view = view
        self._status_bar.set_state(view.connection_state)
        self._update_enemies(view)
        self._update_picks(view)
        self._ban_panel.update_suggestions(
            view.ban_suggestions, self._icon_for_key,
        )
        # Floating lobby stats widget runs alongside the main overlay's
        # champ-select panel and reads the same SessionView. Held here so
        # it gets every refresh without an extra subscription path.
        lobby = getattr(self, "_lobby_stats", None)
        if lobby is not None:
            lobby.update_view(view)
        # Champ-select state machine: pivot which panels are visible based
        # on the current sub-phase (bans → picks → loading-screen profiles).
        self._apply_champ_select_subphase(view)

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
                profile = view.enemy_profiles.get(member.cell_id)
                row.set_profile(
                    profile,
                    # Global maps so mains-icons can resolve any
                    # champion, not just the 5 enemy picks.
                    champion_names=view.all_champion_names or view.enemy_names,
                    champion_keys=view.all_champion_keys,
                    icon_lookup=self._icon_for_key,
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
        for idx, s in enumerate(view.suggestions, start=1):
            icon = self._icon_for_key(s.champion_key)
            build = view.suggestion_builds.get(s.champion_key)
            reasons = view.suggestion_build_reasons.get(s.champion_key) or []
            card = PickCard(
                s, icon=icon, build=build, rank=idx,
                build_reasons=reasons,
            )
            card.apply_build_requested.connect(self.apply_build_requested.emit)
            card.pick_hover_requested.connect(self.pick_hover_requested.emit)
            self._picks_container.addWidget(card)

    # -- in-game panels visibility ---------------------------------------

    def update_lcda_snapshot(self, snapshot: LcdaSnapshot | None) -> None:
        """Forward LCDA ticks to in-game panels — but only the ones the user
        hasn't toggled off via the title-bar buttons. Also drives the
        champselect <-> overlay mode switch so the window only goes
        always-on-top + transparent when there's a real game running.
        """
        if self._panel_allowed("summoners"):
            self._summoner_tracker.update_snapshot(snapshot)
        if self._panel_allowed("spikes"):
            self._power_spike_panel.update_snapshot(snapshot)
        self.set_phase_visibility(
            in_champ_select=False,
            in_game=snapshot is not None,
        )
        target = "overlay" if snapshot is not None else "champselect"
        if target != self._current_mode:
            self._switch_mode(target)
        if snapshot is not None and not getattr(self, "_borderless_hint_shown", False):
            self._borderless_hint_shown = True
            # We used to print a "switch to Borderless" warning here based on
            # WS_POPUP+WS_CAPTION flag inspection. That heuristic can't tell
            # Borderless and Fullscreen Exclusive apart (they share flags),
            # so the warning often misfired. Replaced with a neutral status:
            # the user can see whether the overlay shows up or not.
            from . import styles as _styles
            self._status_bar.set_info(
                "Overlay aktiv — falls nicht sichtbar: League auf Borderless umstellen",
                color=_styles.SUCCESS,
            )

    def _switch_mode(self, mode: str) -> None:
        """champselect = wide, opaque, normal window — visible.
        overlay = the main window hides entirely; the floating mini-widgets
        (scoreboard, minimap timers, ...) take over.

        Window-flag handling is delegated to ``window_flags`` so all four
        consumers (this method + tray show + startup + reconnect) call
        the same idempotent helper."""
        from .. import window_flags
        if mode not in ("champselect", "overlay"):
            return
        self._current_mode = mode
        if mode == "overlay":
            # In-game: stash the main window. The floating widgets handle
            # their own visibility via update_snapshot. Apply overlay flags
            # so when the user does Show via tray we come back correctly.
            window_flags.apply_overlay_flags(self)
            self.hide()
            return

        window_flags.apply_champselect_flags(self)
        # Allow auto-pin to fire again next time we re-enter overlay.
        self._pinned_for_session = False
        target_w = max(self._persisted.width, 560)
        target_h = self.height() if self._body.isVisible() else self.height()
        clamped_w, clamped_h = self._clamp_to_screen(target_w, target_h)
        self.resize(clamped_w, clamped_h)
        self.setWindowOpacity(1.0)
        self.show()

    def _pin_to_league_window(self) -> None:
        """Locate League's window via Win32 and park ourselves at its
        right edge. Falls back to the screen-edge anchor on non-Windows
        or when League isn't running."""
        from ..lcu.window import find_league_window

        info = find_league_window()
        if info is None:
            return
        # If the user prefers a custom-saved position, only auto-pin once
        # per game — set a sentinel on the first pin to avoid fighting
        # subsequent manual moves.
        if getattr(self, "_pinned_for_session", False):
            return
        self._pinned_for_session = True
        margin = 8
        x = info.right - self.width() - margin
        # Clamp Y inside the league window vertically.
        y = info.top + margin
        # Honour the multi-screen safety check we already do for restored
        # positions — never end up off-screen.
        from PyQt6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        for screen in screens:
            geo = screen.availableGeometry()
            if geo.left() <= x <= geo.right() - 50:
                self.move(x, y)
                return
        # If the computed pin lands off-screen (e.g. League on disconnected
        # monitor), fall back to anchored placement.
        self._anchor_to_screen_edge(self._persisted.anchor)

    def _panel_allowed(self, key: str) -> bool:
        """Whether the user-level toggle for ``key`` permits rendering."""
        if not self._save_state:
            return True
        return {
            "summoners":  self._persisted.show_summoners,
            "spikes":     self._persisted.show_spikes,
        }.get(key, True)

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
        # The whole side-by-side champ-select row hides as one block; that
        # keeps the in-game overlay nice and narrow.
        self._champselect_row.setVisible(in_champ_select or not in_game)

    # -- champ-select state machine --------------------------------------

    # Width per phase. Champ-select needs room for enemy panel + picks
    # side-by-side; in-game shrinks back to a narrow strip. Stored as
    # min/preferred widths so the user's manual resize still wins on
    # a re-drag, but our pivots set a sane starting point on phase
    # transitions.
    _W_CHAMP_SELECT = 720
    _W_INGAME = 420

    def _apply_champ_select_subphase(self, view: SessionView) -> None:
        """Drive panel visibility from the session's display_subphase.

        Each subphase exposes a different focus:
          * ``ban`` — ban suggestions front and center, picks hidden.
          * ``pick`` / ``finalization`` — enemy counters + own picks
            visible, ban panel collapsed (already locked-in).
          * ``loading`` — both teams' player-profile dump (lobby_stats);
            ban + pick panels hidden, the floating lobby widget owns
            the screen real estate.
          * ``idle`` / ``in_game`` — champ-select row entirely hidden
            so the overlay stays compact during the live game.
        """
        session = view.session
        subphase = session.display_subphase() if session is not None else "idle"

        if subphase == "in_game":
            self._champselect_row.setVisible(False)
            self._ban_panel.setVisible(False)
            self._set_width(self._W_INGAME)
            return

        if subphase == "idle":
            # Pre-lobby / disconnected — keep the whole champ-select
            # block visible at narrow width so the user sees the
            # connection-status hint instead of an empty void.
            self._champselect_row.setVisible(True)
            self._set_width(self._W_INGAME)
            return

        # Active champ-select — wider window, then per-subphase pivots.
        self._champselect_row.setVisible(True)
        self._set_width(self._W_CHAMP_SELECT)

        if subphase == "ban":
            # Ban-step focus: bans front, picks suppressed.
            self._ban_panel.setVisible(True)
            self._picks_panel.setVisible(False)
        elif subphase in ("pick", "finalization", "planning"):
            # Pick/finalization: counters + suggestions take over,
            # bans collapse (history, not actionable anymore).
            self._ban_panel.setVisible(False)
            self._picks_panel.setVisible(True)
        elif subphase == "loading":
            # Loading screen → lobby_stats floating widget is the
            # primary surface. Hide both ban + pick panels in the
            # main overlay so it doesn't compete.
            self._ban_panel.setVisible(False)
            self._picks_panel.setVisible(False)
        else:
            # Unknown phase — leave everything visible as a safe
            # fallback rather than silently hiding.
            self._ban_panel.setVisible(True)
            self._picks_panel.setVisible(True)

    def _set_width(self, target_w: int) -> None:
        """Resize the window to ``target_w`` while respecting the user's
        explicit drag-to-resize. Only acts when the current width is
        materially different (>40px) to avoid jitter on every refresh."""
        current = self.width()
        if abs(current - target_w) <= 40:
            return
        clamped_w, clamped_h = self._clamp_to_screen(target_w, self.height())
        self.resize(clamped_w, clamped_h)

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
                # Nudge the window up if its bottom edge would clip past
                # the screen bottom (e.g. saved on a bigger monitor, opened
                # on a laptop screen).
                bottom_overhang = (state.y + self.height()) - geo.bottom()
                target_y = state.y - max(0, bottom_overhang) - 8
                self.move(state.x, max(geo.top() + 8, target_y))
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

    def _clamp_to_screen(self, want_w: int, want_h: int) -> tuple[int, int]:
        """Cap requested size so we never exceed the available screen.

        On a 1366x768 laptop with a 40px taskbar the usable height is ~728
        which is less than the persisted default of 720 + chrome. Without
        clamping, the bottom of the panel renders past the monitor.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return want_w, want_h
        geo = screen.availableGeometry()
        # Leave a small margin so the user can still grab the bottom edge.
        max_w = max(280, geo.width() - 16)
        max_h = max(360, geo.height() - 32)
        return min(want_w, max_w), min(want_h, max_h)

    def _open_settings(self) -> None:
        from .settings_dialog import open_settings
        # Pass the hotkey service through if __main__ wired one — lets
        # the dialog do live re-registration via update_binding.
        hotkeys = getattr(self, "_hotkeys", None)
        if open_settings(self, hotkey_service=hotkeys):
            self.settings_changed.emit()

    def _on_passthrough_toggled(self, on: bool) -> None:
        """Click-through mode: body widget ignores all mouse events so they
        pass through to League. The title bar stays interactive (it's NOT
        a child of _body) so users can always toggle back. Also dim the
        body slightly when click-through is active for visual feedback."""
        self._body.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        # Slight extra fade in passthrough mode so it visually reads as
        # "frozen / inactive" — but not invisible.
        if on:
            base_opacity = self._persisted.opacity if self._save_state else 0.92
            self.setWindowOpacity(max(0.5, base_opacity * 0.85))
        else:
            base_opacity = self._persisted.opacity if self._save_state else 0.92
            self.setWindowOpacity(base_opacity if self._current_mode == "overlay" else 1.0)
        self._title_bar.set_passthrough(on)

    def _on_opacity_changed(self, opacity: float) -> None:
        self.setWindowOpacity(opacity)
        if self._save_state:
            self._persisted.opacity = opacity
            overlay_config.save(self._persisted)

    def _on_panel_toggled(self, key: str, visible: bool) -> None:
        if key == "summoners":
            self._summoner_tracker.setVisible(visible)
            if self._save_state:
                self._persisted.show_summoners = visible
        elif key == "spikes":
            self._power_spike_panel.setVisible(visible)
            if self._save_state:
                self._persisted.show_spikes = visible
        if self._save_state:
            overlay_config.save(self._persisted)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._save_state:
            self._persisted.x = self.x()
            self._persisted.y = self.y()
            self._persisted.width = self.width()
            self._persisted.height = self.height() if self._body.isVisible() else self._persisted.height
            self._persisted.collapsed = not self._body.isVisible()
            overlay_config.save(self._persisted)
        # When a tray icon is active, X should only hide the main window,
        # not quit the whole app — the floating widgets + tray live on.
        # Quit only via the tray menu.
        if getattr(self, "_tray", None) is not None:
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
        # Tool windows + frameless flags don't always trigger Qt's "last
        # window closed" path, so the QApplication can stay alive forever.
        # Force the quit signal here — qasync's loop.stop is already wired
        # to QApplication.aboutToQuit so this kicks the whole shutdown chain.
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
