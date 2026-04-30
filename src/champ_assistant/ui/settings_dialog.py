"""Settings dialog — tabbed layout for clarity.

Tabs (in order shown to the user):

  * Widgets       — main panel sections + floating widget visibility
                    + reset-layout action
  * API Keys      — Riot Web API + optional LLM provider
  * Hotkeys       — five global hotkey bindings, click-to-rebind
  * Vision (Exp)  — experimental Windows-only color-heuristic
                    detectors (camp clearing + scoreboard visibility)
  * Diagnostics   — diagnostics logging, telemetry, update checks

Persistence:
  API keys + LLM key go through ``secrets`` (OS keyring).
  Everything else lives in ``overlay_config.OverlayState``.
  Hotkeys mirror to ``hotkey_config.HotkeyConfig``.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import hotkey_config, overlay_config, secrets
from ..profiling.riot_api import PLATFORM_HOSTS
from . import styles


class SettingsDialog(QDialog):
    """Modal settings dialog. Emits ``settings_changed`` on accept."""

    settings_changed = pyqtSignal()

    HOTKEY_ACTIONS = (
        ("toggle_overlay",    "Toggle overlay"),
        ("toggle_lock",       "Toggle click-through"),
        ("reset_positions",   "Reset widget positions"),
        ("reset_layout",      "Reset widget layout"),
        ("toggle_scoreboard", "Toggle scoreboard"),
        ("toggle_insight",    "Toggle insight panel"),
    )

    def __init__(
        self,
        parent=None,  # type: ignore[no-untyped-def]
        hotkey_service=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self._hotkey_service = hotkey_service
        self.setWindowTitle("Settings")
        self.setMinimumWidth(540)
        self.setMinimumHeight(560)
        # Inherits styling from the global stylesheet (QLineEdit/QComboBox/
        # QPushButton tokens) so we only need a backdrop hint here.
        self.setStyleSheet(
            f"QDialog {{ background-color: {styles.BG_PRIMARY}; }}"
        )

        # Persisted overlay state — all checkbox/toggle widgets bind
        # against this single object, _on_save writes it back once.
        self._display_state = overlay_config.load()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"font-size: {styles.FS_TITLE}px; font-weight: 700;"
            f" color: {styles.TEXT_PRIMARY}; letter-spacing: -0.2px;"
        )
        outer.addWidget(title)

        tabs = QTabWidget()
        tabs.setStyleSheet(_tab_widget_stylesheet())
        tabs.addTab(self._build_widgets_tab(), "Widgets")
        tabs.addTab(self._build_api_tab(), "API Keys")
        tabs.addTab(self._build_hotkeys_tab(), "Hotkeys")
        tabs.addTab(self._build_vision_tab(), "Vision (experimental)")
        tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        outer.addWidget(tabs, 1)

        outer.addWidget(self._build_button_row())

    # ------------------------------------------------------------------
    # Tab builders — each returns a ready-to-add QWidget
    # ------------------------------------------------------------------

    def _build_widgets_tab(self) -> QWidget:
        """Visibility toggles for both main-overlay sections AND the
        three floating widgets, plus a layout-reset action."""
        page = _scrolling_page()
        body = _vertical(page)

        body.addWidget(_section_header("Main Overlay Sections"))
        self._cb_summoners = _checkbox(
            "Show Summoner Tracker (enemy spell cooldowns)",
            self._display_state.show_summoners,
        )
        self._cb_spikes = _checkbox(
            "Show Power-Spike panel",
            self._display_state.show_spikes,
        )
        for cb in (self._cb_summoners, self._cb_spikes):
            body.addWidget(cb)

        body.addWidget(_section_header("Floating Mini-Widgets"))
        self._cb_scoreboard = _checkbox(
            "Scoreboard widget (kills + gold delta + objectives)",
            self._display_state.show_scoreboard,
        )
        self._cb_minimap = _checkbox(
            "Minimap-Timers widget (Dragon / Baron / camp predictions)",
            self._display_state.show_minimap_timers,
        )
        self._cb_lobby = _checkbox(
            "Lobby-Stats widget (champ-select stats summary)",
            self._display_state.show_lobby_stats,
        )
        for cb in (self._cb_scoreboard, self._cb_minimap, self._cb_lobby):
            body.addWidget(cb)

        body.addWidget(_section_header("Layout"))
        reset_row = QHBoxLayout()
        reset_label = QLabel("Reset widget positions to defaults")
        reset_label.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: {styles.FS_BODY}px;"
        )
        reset_row.addWidget(reset_label, 1)
        reset_btn = _flat_button("Reset Layout")
        reset_btn.clicked.connect(self._on_reset_layout)
        reset_row.addWidget(reset_btn)
        body.addLayout(reset_row)

        body.addWidget(_hint_label(
            "Widget visibility changes apply on the next launch. "
            "Reset Layout takes effect immediately."
        ))
        body.addStretch(1)
        return page

    def _build_api_tab(self) -> QWidget:
        """Riot Web API + optional LLM provider for live counter
        lookup. All keys go through ``secrets`` (OS keyring)."""
        page = _scrolling_page()
        body = _vertical(page)

        body.addWidget(_section_header("Riot Web API"))
        riot_form = QFormLayout()
        riot_form.setHorizontalSpacing(12)
        riot_form.setVerticalSpacing(8)

        self._riot_key = QLineEdit()
        self._riot_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._riot_key.setPlaceholderText("RGAPI-...")
        self._riot_key.setText(secrets.riot_api_key())
        riot_form.addRow("API Key", self._riot_key)

        self._riot_region = QComboBox()
        for region in PLATFORM_HOSTS:
            self._riot_region.addItem(region)
        idx = self._riot_region.findText(secrets.riot_region())
        if idx >= 0:
            self._riot_region.setCurrentIndex(idx)
        riot_form.addRow("Region", self._riot_region)
        body.addLayout(riot_form)

        riot_help = QLabel(
            f'Hol einen Dev-Key auf <a style="color:{styles.ACCENT};"'
            ' href="https://developer.riotgames.com">developer.riotgames.com</a>'
            " — gilt 24h und reicht fuer Solo-Tests."
        )
        riot_help.setOpenExternalLinks(True)
        riot_help.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
        )
        riot_help.setWordWrap(True)
        body.addWidget(riot_help)

        body.addWidget(_section_header("Live Counter Lookup (optional)"))
        llm_form = QFormLayout()
        llm_form.setHorizontalSpacing(12)
        llm_form.setVerticalSpacing(8)

        self._llm_provider = QComboBox()
        for label, value in (
            ("OpenRouter (empfohlen)", "openrouter"),
            ("Groq", "groq"),
            ("Google Gemini", "gemini"),
        ):
            self._llm_provider.addItem(label, value)
        idx = max(0, self._llm_provider.findData(secrets.llm_provider()))
        self._llm_provider.setCurrentIndex(idx)
        llm_form.addRow("Provider", self._llm_provider)

        self._llm_key = QLineEdit()
        self._llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_key.setPlaceholderText("API-Key")
        self._llm_key.setText(secrets.llm_api_key())
        llm_form.addRow("API Key", self._llm_key)
        body.addLayout(llm_form)

        self._llm_help = QLabel("")
        self._llm_help.setOpenExternalLinks(True)
        self._llm_help.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
        )
        self._llm_help.setWordWrap(True)
        body.addWidget(self._llm_help)
        self._llm_provider.currentIndexChanged.connect(self._refresh_llm_help)
        self._refresh_llm_help()

        body.addStretch(1)
        return page

    def _build_hotkeys_tab(self) -> QWidget:
        page = _scrolling_page()
        body = _vertical(page)

        body.addWidget(_section_header("Global Hotkeys"))

        self._hotkey_buttons: dict[str, QPushButton] = {}
        for action, display_name in self.HOTKEY_ACTIONS:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(display_name)
            label.setStyleSheet(
                f"color: {styles.TEXT_PRIMARY}; font-size: {styles.FS_BODY}px;"
            )
            row.addWidget(label, 1)

            current = self._current_hotkey_label(action)
            btn = QPushButton(current)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(140)
            btn.setStyleSheet(_hotkey_button_stylesheet())
            btn.clicked.connect(
                lambda _checked=False, a=action: self._capture_hotkey(a)
            )
            self._hotkey_buttons[action] = btn
            row.addWidget(btn)
            body.addLayout(row)

        body.addWidget(_hint_label(
            "Klick auf einen Hotkey öffnet den Aufnahme-Dialog. "
            "Auf macOS / Linux laufen die globalen Hotkeys nicht "
            "(Win32 RegisterHotKey-only)."
        ))
        body.addStretch(1)
        return page

    def _build_vision_tab(self) -> QWidget:
        """Experimental color-heuristic vision features. Both are
        Windows-only and require capture-region calibration; honest
        warning at the top of the tab."""
        page = _scrolling_page()
        body = _vertical(page)

        warning = QLabel(
            "⚠ Experimentell — funktioniert nur unter Windows und "
            "benötigt Kalibrierung der Capture-Region für deine "
            "Auflösung. Defaults zielen auf 1080p mit Standard-Minimap."
        )
        warning.setStyleSheet(
            f"color: {styles.WARNING}; font-size: {styles.FS_LABEL}px;"
            f" background: {styles.BG_TERTIARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            " padding: 8px 10px;"
        )
        warning.setWordWrap(True)
        body.addWidget(warning)

        body.addWidget(_section_header("Camp Detection"))
        self._cb_auto_camp = _checkbox(
            "Auto-Detect Jungle Camps (color heuristic)",
            self._display_state.enable_auto_camp_detection,
        )
        body.addWidget(self._cb_auto_camp)
        body.addWidget(_hint_label(
            "Setzt den Engine-Anchor wenn ein Camp-Icon aus der "
            "Minimap verschwindet. Stage A: einfache Farb-Heuristik. "
            "Bias zu false-negative — verpasste Clears lassen den "
            "deterministischen Zyklus unverändert."
        ))

        body.addWidget(_section_header("Scoreboard Detection"))
        self._cb_scoreboard_detect = _checkbox(
            "Auto-Detect Scoreboard (TAB-Anzeige)",
            self._display_state.enable_scoreboard_detection,
        )
        body.addWidget(self._cb_scoreboard_detect)
        body.addWidget(_hint_label(
            "Erkennt das in-game Scoreboard via "
            "low-variance + dark-pixel Heuristik im Top-Center-Bereich. "
            "Triggert die Gold-Diff-Anzeige während TAB gehalten wird. "
            "Manuelle Alternative: Ctrl+Alt+B."
        ))
        body.addStretch(1)
        return page

    def _build_diagnostics_tab(self) -> QWidget:
        page = _scrolling_page()
        body = _vertical(page)

        body.addWidget(_section_header("Low Resource Mode"))
        self._cb_low_resource = _checkbox(
            "Low Resource Mode (für schwächere Rechner / Streaming)",
            self._display_state.low_resource_mode,
        )
        body.addWidget(self._cb_low_resource)
        body.addWidget(_hint_label(
            "Master-Switch: deaktiviert Vision-Detection, Telemetry, "
            "Update-Check und cap't Render-Rate auf 10 FPS. Andere "
            "Toggle-Settings bleiben gespeichert; LRM überschreibt "
            "sie nur zur Laufzeit."
        ))

        body.addWidget(_section_header("Focus Mode"))
        self._cb_focus = _checkbox(
            "Focus Mode (nur Top-1 Empfehlung, weniger Cognitive Load)",
            self._display_state.focus_mode,
        )
        body.addWidget(self._cb_focus)
        body.addWidget(_hint_label(
            "Statt Top-3 Recommendations rendert das Panel nur die "
            "wichtigste. Hilft im stress-light wenn man eh nur EINE "
            "Sache anzeigen will."
        ))

        body.addWidget(_section_header("Logging"))
        self._cb_diagnostics = _checkbox(
            "Diagnose-Logging (CPU / Speicher / FPS alle 10s)",
            self._display_state.diagnostics_enabled,
        )
        body.addWidget(self._cb_diagnostics)
        body.addWidget(_hint_label(
            "Schreibt in app.log neben dem Hauptlog. "
            "Hilft bei Performance-Auditing."
        ))

        body.addWidget(_section_header("Telemetry"))
        self._cb_telemetry = _checkbox(
            "Telemetrie-Aufzeichnung (lokal, append-only JSONL)",
            self._display_state.enable_telemetry,
        )
        body.addWidget(self._cb_telemetry)
        body.addWidget(_hint_label(
            "Lokale Event-Logs für UX-Auswertung. "
            "Keine Netzwerk-Übertragung, keine User-Inputs / Chat / "
            "Account-Daten. Speicherort: telemetry.jsonl im "
            "Config-Verzeichnis."
        ))

        body.addWidget(_section_header("Updates"))
        self._cb_update_check = _checkbox(
            "Update-Check beim Start (GitHub Releases)",
            self._display_state.enable_update_check,
        )
        body.addWidget(self._cb_update_check)
        body.addWidget(_hint_label(
            "Einmaliger HTTPS-Call gegen api.github.com beim "
            "App-Start. Aus für Metered Connections / "
            "Privacy-Setups."
        ))
        body.addStretch(1)
        return page

    # ------------------------------------------------------------------
    # Action wiring
    # ------------------------------------------------------------------

    def _build_button_row(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_btn is not None:
            save_btn.setStyleSheet(
                f"QPushButton {{"
                f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                f" stop:0 {styles.ACCENT_BRIGHT}, stop:1 {styles.ACCENT});"
                f" color: white; border: none;"
                f" border-radius: 6px; padding: 6px 18px;"
                f" font-weight: 700; font-size: {styles.FS_BODY}px; }}"
                f" QPushButton:hover {{ background: {styles.ACCENT_BRIGHT}; }}"
                f" QPushButton:pressed {{ background: {styles.ACCENT}; }}"
            )
        if cancel_btn is not None:
            cancel_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent;"
                f" color: {styles.TEXT_SECONDARY};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: 6px; padding: 6px 16px;"
                f" font-weight: 600; font-size: {styles.FS_BODY}px; }}"
                f" QPushButton:hover {{ background-color: {styles.BG_TERTIARY};"
                f" color: {styles.TEXT_PRIMARY}; }}"
            )
        return buttons

    def _on_save(self) -> None:
        # Secrets (keyring) ----------------------------------------------
        secrets.set_riot_api_key(self._riot_key.text().strip())
        secrets.set_riot_region(self._riot_region.currentText())
        secrets.set_llm_provider(self._llm_provider.currentData() or "openrouter")
        secrets.set_llm_api_key(self._llm_key.text().strip())
        if self._llm_provider.currentData() == "groq":
            secrets.set_groq_api_key(self._llm_key.text().strip())

        # Overlay state (single dataclass, single save call) -------------
        s = self._display_state
        s.show_summoners            = self._cb_summoners.isChecked()
        s.show_spikes               = self._cb_spikes.isChecked()
        s.show_scoreboard           = self._cb_scoreboard.isChecked()
        s.show_minimap_timers       = self._cb_minimap.isChecked()
        s.show_lobby_stats          = self._cb_lobby.isChecked()
        s.diagnostics_enabled       = self._cb_diagnostics.isChecked()
        s.enable_auto_camp_detection = self._cb_auto_camp.isChecked()
        s.enable_scoreboard_detection = self._cb_scoreboard_detect.isChecked()
        s.enable_update_check       = self._cb_update_check.isChecked()
        s.enable_telemetry          = self._cb_telemetry.isChecked()
        s.low_resource_mode         = self._cb_low_resource.isChecked()
        s.focus_mode                = self._cb_focus.isChecked()
        overlay_config.save(s)

        self.settings_changed.emit()
        self.accept()

    def _on_reset_layout(self) -> None:
        from .. import layout as _layout
        from .floating_widget import FloatingWidget

        _layout.store().reset()
        for widget in FloatingWidget._instances:
            x, y = widget.DEFAULT_POS
            w, h = widget.DEFAULT_SIZE
            widget.setGeometry(x, y, w, h)
            widget.show()

    # ------------------------------------------------------------------
    # Hotkey capture flow (unchanged from the prior implementation)
    # ------------------------------------------------------------------

    def _current_hotkey_label(self, action: str) -> str:
        if self._hotkey_service is not None:
            binding = self._hotkey_service.get_binding(action)
            if binding is not None:
                return binding.label
        cfg = hotkey_config.load()
        return cfg.hotkeys.get(action, hotkey_config.DEFAULT_HOTKEYS.get(action, ""))

    def _capture_hotkey(self, action: str) -> None:
        from .hotkey_capture import KeyCaptureDialog

        current = self._current_hotkey_label(action)
        dlg = KeyCaptureDialog(parent=self, current_label=current)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_label = dlg.captured_combo()
        if not new_label or new_label == current:
            return

        if self._hotkey_service is not None:
            ok, msg = self._hotkey_service.update_binding(action, new_label)
            if not ok:
                self._hotkey_buttons[action].setText(current)
                self._show_hotkey_error(action, msg)
                return
        cfg = hotkey_config.load()
        cfg.hotkeys[action] = new_label
        hotkey_config.save(cfg)
        self._hotkey_buttons[action].setText(new_label)

    def _show_hotkey_error(self, action: str, msg: str) -> None:
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Hotkey conflict")
        box.setText(
            f"Couldn't bind that combo: {msg}.\n\n"
            "The previous hotkey is still active."
        )
        box.exec()

    def _refresh_llm_help(self) -> None:
        provider = self._llm_provider.currentData() or "openrouter"
        urls = {
            "openrouter": "https://openrouter.ai/keys",
            "groq":       "https://console.groq.com/keys",
            "gemini":     "https://aistudio.google.com/apikey",
        }
        url = urls.get(provider, urls["openrouter"])
        self._llm_help.setText(
            "Hol einen kostenlosen Key auf "
            f'<a style="color:{styles.ACCENT};" href="{url}">{url}</a> '
            "und speichere ihn hier. Ohne Key benutzt der Assistant nur die"
            " mitgelieferten Counter-Daten."
        )


# ----------------------------------------------------------------------
# Tab-internal helpers
# ----------------------------------------------------------------------

def _scrolling_page() -> QWidget:
    """Container widget used as a tab page. Tabs share their parent
    QTabWidget's height; long content scrolls naturally inside its
    tab if needed."""
    page = QWidget()
    page.setStyleSheet(f"background: {styles.BG_PRIMARY};")
    return page


def _vertical(parent: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(
        styles.SPACING_GRID, styles.SPACING_GRID,
        styles.SPACING_GRID, styles.SPACING_GRID,
    )
    layout.setSpacing(styles.SPACING_GRID)
    return layout


def _section_header(text: str) -> QLabel:
    """Section title with a small accent dot prefix — mirrors the
    title-bar pattern from v1.7.0 so the visual rhythm is consistent
    across the app. Compact letter-spaced uppercase for that
    modern-overlay vibe."""
    label = QLabel(f"●  {text.upper()}")
    label.setStyleSheet(
        f"color: {styles.ACCENT};"
        f" font-size: {styles.FS_LABEL}px;"
        " font-weight: 700; text-transform: uppercase;"
        " letter-spacing: 1.6px; padding: 12px 0 4px 0;"
    )
    return label


def _hint_label(text: str) -> QLabel:
    """Caption-style hint underneath a control. Slightly indented so
    it visually anchors to the control above without competing with
    the next section header."""
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {styles.TEXT_MUTED};"
        f" font-size: {styles.FS_CAPTION}px;"
        f" padding-left: 24px; padding-bottom: 6px;"
        " line-height: 1.4;"
    )
    label.setWordWrap(True)
    return label


def _checkbox(label: str, checked: bool) -> QCheckBox:
    return _styled_checkbox(label, checked)


def _flat_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
        f" color: {styles.TEXT_SECONDARY};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS_SMALL}px;"
        f" padding: 5px 14px; font-size: {styles.FS_LABEL}px; font-weight: 600; }}"
        f" QPushButton:hover {{ border-color: {styles.WARNING};"
        f" color: {styles.TEXT_PRIMARY}; }}"
    )
    return btn


def _hotkey_button_stylesheet() -> str:
    return (
        f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
        f" color: {styles.ACCENT};"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: {styles.RADIUS_SMALL}px;"
        f" padding: 5px 12px; font-family: {styles.FONT_MONO};"
        f" font-weight: 700; font-size: {styles.FS_LABEL}px; }}"
        f" QPushButton:hover {{ border-color: {styles.ACCENT};"
        f" background-color: {styles.BG_ELEVATED}; }}"
    )


def _tab_widget_stylesheet() -> str:
    """Tabs inherit the panel-token visual language: dark background,
    accent-bordered active tab, muted inactive tabs."""
    return (
        # Tab area frame
        f"QTabWidget::pane {{"
        f"  background-color: {styles.BG_SECONDARY};"
        f"  border: 1px solid {styles.BORDER};"
        f"  border-radius: {styles.RADIUS_SMALL}px;"
        f"  top: -1px;"
        " }"
        # Tab bar (the strip itself)
        f"QTabBar::tab {{"
        f"  background-color: {styles.BG_TERTIARY};"
        f"  color: {styles.TEXT_MUTED};"
        f"  border: 1px solid {styles.BORDER};"
        f"  border-bottom: none;"
        f"  border-top-left-radius: {styles.RADIUS_SMALL}px;"
        f"  border-top-right-radius: {styles.RADIUS_SMALL}px;"
        f"  padding: 8px 16px;"
        f"  font-size: {styles.FS_BODY}px;"
        f"  font-weight: 600;"
        f"  margin-right: 2px;"
        " }"
        f"QTabBar::tab:selected {{"
        f"  background-color: {styles.BG_SECONDARY};"
        f"  color: {styles.ACCENT};"
        " }"
        f"QTabBar::tab:hover:!selected {{"
        f"  color: {styles.TEXT_PRIMARY};"
        " }"
    )


def _styled_checkbox(label: str, checked: bool) -> QCheckBox:
    cb = QCheckBox(label)
    cb.setChecked(checked)
    cb.setCursor(Qt.CursorShape.PointingHandCursor)
    cb.setStyleSheet(
        f"QCheckBox {{ color: {styles.TEXT_PRIMARY};"
        f" font-size: {styles.FS_BODY}px;"
        f" spacing: 8px; padding: 2px 0; }}"
        f" QCheckBox::indicator {{ width: 16px; height: 16px;"
        f" border: 1px solid {styles.BORDER};"
        f" border-radius: 3px; background-color: {styles.BG_TERTIARY}; }}"
        f" QCheckBox::indicator:hover {{ border-color: {styles.ACCENT}; }}"
        f" QCheckBox::indicator:checked {{ background-color: {styles.ACCENT};"
        f" border-color: {styles.ACCENT}; }}"
    )
    return cb


def open_settings(parent=None, hotkey_service=None) -> bool:  # type: ignore[no-untyped-def]
    """Helper used by the title bar gear button. Returns True on save."""
    dlg = SettingsDialog(parent, hotkey_service=hotkey_service)
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        dlg.move(
            geo.center().x() - dlg.sizeHint().width() // 2,
            geo.center().y() - dlg.sizeHint().height() // 2,
        )
    return dlg.exec() == QDialog.DialogCode.Accepted
