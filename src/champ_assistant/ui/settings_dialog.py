"""Settings dialog for API keys + region.

Persists via ``champ_assistant.secrets`` (keyring-backed) so the same
keys carry over between launches without leaving plaintext on disk.
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
    QVBoxLayout,
)

from .. import hotkey_config, overlay_config, secrets
from ..profiling.riot_api import PLATFORM_HOSTS
from . import styles


class SettingsDialog(QDialog):
    """Modal settings dialog. Emits ``settings_changed`` on accept."""

    settings_changed = pyqtSignal()

    def __init__(
        self,
        parent=None,  # type: ignore[no-untyped-def]
        hotkey_service=None,  # type: ignore[no-untyped-def]
    ) -> None:
        super().__init__(parent)
        self._hotkey_service = hotkey_service
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        # Inherits styling from the global stylesheet (QLineEdit/QComboBox/
        # QPushButton tokens) so we only need a backdrop hint here.
        self.setStyleSheet(
            f"QDialog {{ background-color: {styles.BG_PRIMARY}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"font-size: {styles.FS_TITLE}px; font-weight: 700;"
            f" color: {styles.TEXT_PRIMARY}; letter-spacing: -0.2px;"
        )
        outer.addWidget(title)

        # -- Riot API -----------------------------------------------------
        riot_section = QLabel("Riot Web API")
        riot_section.setObjectName("sectionTitle")
        riot_section.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px;"
            " padding-top: 4px;"
        )
        outer.addWidget(riot_section)

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
        current_region = secrets.riot_region()
        idx = self._riot_region.findText(current_region)
        if idx >= 0:
            self._riot_region.setCurrentIndex(idx)
        riot_form.addRow("Region", self._riot_region)

        outer.addLayout(riot_form)

        riot_help = QLabel(
            f'Hol einen Dev-Key auf <a style="color:{styles.ACCENT};"'
            ' href="https://developer.riotgames.com">developer.riotgames.com</a>'
            " — gilt 24h und reicht fuer Solo-Tests."
        )
        riot_help.setOpenExternalLinks(True)
        riot_help.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
        riot_help.setWordWrap(True)
        outer.addWidget(riot_help)

        # -- LLM provider (live counters) --------------------------------
        llm_section = QLabel("Live Counter Lookup (optional)")
        llm_section.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 11px; font-weight: 700;"
            " text-transform: uppercase; letter-spacing: 0.8px; padding-top: 8px;"
        )
        outer.addWidget(llm_section)

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
        outer.addLayout(llm_form)

        self._llm_help = QLabel("")
        self._llm_help.setOpenExternalLinks(True)
        self._llm_help.setStyleSheet(f"color: {styles.TEXT_MUTED}; font-size: 11px;")
        self._llm_help.setWordWrap(True)
        outer.addWidget(self._llm_help)
        self._llm_provider.currentIndexChanged.connect(self._refresh_llm_help)
        self._refresh_llm_help()

        # -- Display ------------------------------------------------------
        # Consolidates the floating-widget visibility flags + the layout
        # reset action (previously only reachable via Ctrl+Alt+R) + the
        # diagnostics toggle. One section, one source of truth.
        display_section = QLabel("Display")
        display_section.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px;"
            " padding-top: 8px;"
        )
        outer.addWidget(display_section)

        ovc_state = overlay_config.load()
        self._display_state = ovc_state  # held for save

        self._cb_scoreboard = _styled_checkbox(
            "Scoreboard-Widget anzeigen", ovc_state.show_scoreboard,
        )
        self._cb_minimap = _styled_checkbox(
            "Minimap-Timer-Widget anzeigen", ovc_state.show_minimap_timers,
        )
        self._cb_lobby = _styled_checkbox(
            "Lobby-Stats-Widget anzeigen", ovc_state.show_lobby_stats,
        )
        self._cb_diagnostics = _styled_checkbox(
            "Diagnose-Logging (CPU / Speicher / FPS alle 10s)",
            ovc_state.diagnostics_enabled,
        )
        for cb in (self._cb_scoreboard, self._cb_minimap, self._cb_lobby, self._cb_diagnostics):
            outer.addWidget(cb)

        reset_row = QHBoxLayout()
        reset_row.setSpacing(10)
        reset_label = QLabel("Widget-Layout")
        reset_label.setStyleSheet(
            f"color: {styles.TEXT_PRIMARY}; font-size: {styles.FS_BODY}px;"
        )
        reset_row.addWidget(reset_label, 1)

        reset_btn = QPushButton("Auf Standard zurücksetzen")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.setStyleSheet(
            f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
            f" color: {styles.TEXT_SECONDARY};"
            f" border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS_SMALL}px;"
            f" padding: 5px 14px; font-size: {styles.FS_LABEL}px; font-weight: 600; }}"
            f" QPushButton:hover {{ border-color: {styles.WARNING};"
            f" color: {styles.TEXT_PRIMARY}; }}"
        )
        reset_btn.clicked.connect(self._on_reset_layout)
        reset_row.addWidget(reset_btn)
        outer.addLayout(reset_row)

        # Note + restart hint — visibility/diagnostics changes need a
        # restart since both are read at startup. Honest is better than
        # silently doing nothing or pretending we hot-reload.
        display_hint = QLabel(
            "Änderungen an Widgets / Diagnose werden beim nächsten Start aktiv."
        )
        display_hint.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 11px;"
        )
        display_hint.setWordWrap(True)
        outer.addWidget(display_hint)

        # -- Hotkeys ------------------------------------------------------
        hk_section = QLabel("Global Hotkeys")
        hk_section.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
            " font-weight: 700; text-transform: uppercase; letter-spacing: 1.2px;"
            " padding-top: 8px;"
        )
        outer.addWidget(hk_section)

        # Map of action -> (label, button) so we can update the displayed
        # combo after a successful re-registration.
        self._hotkey_buttons: dict[str, QPushButton] = {}
        for action, display_name in (
            ("toggle_overlay",  "Toggle overlay"),
            ("toggle_lock",     "Toggle click-through"),
            ("reset_positions", "Reset widget positions"),
        ):
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
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {styles.BG_TERTIARY};"
                f" color: {styles.ACCENT};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" padding: 5px 12px; font-family: {styles.FONT_MONO};"
                f" font-weight: 700; font-size: {styles.FS_LABEL}px; }}"
                f" QPushButton:hover {{ border-color: {styles.ACCENT};"
                f" background-color: {styles.BG_ELEVATED}; }}"
            )
            btn.clicked.connect(
                lambda _checked=False, a=action: self._capture_hotkey(a)
            )
            self._hotkey_buttons[action] = btn
            row.addWidget(btn)
            outer.addLayout(row)

        outer.addStretch(1)

        # -- Buttons (Save accent, Cancel flat) -------------------------
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
        outer.addWidget(buttons)

    def _current_hotkey_label(self, action: str) -> str:
        """Get the displayed combo for ``action`` from the live service
        if available, else from the persisted config, else default."""
        if self._hotkey_service is not None:
            binding = self._hotkey_service.get_binding(action)
            if binding is not None:
                return binding.label
        cfg = hotkey_config.load()
        return cfg.hotkeys.get(action, hotkey_config.DEFAULT_HOTKEYS.get(action, ""))

    def _capture_hotkey(self, action: str) -> None:
        """Open the modal capture dialog, then attempt live re-registration."""
        from .hotkey_capture import KeyCaptureDialog

        current = self._current_hotkey_label(action)
        dlg = KeyCaptureDialog(parent=self, current_label=current)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_label = dlg.captured_combo()
        if not new_label or new_label == current:
            return

        # Persist + live re-register. If registration fails (collision),
        # surface the message and leave the previous binding alive.
        if self._hotkey_service is not None:
            ok, msg = self._hotkey_service.update_binding(action, new_label)
            if not ok:
                self._hotkey_buttons[action].setText(current)
                self._show_hotkey_error(action, msg)
                return
        # Mirror to disk so the new combo survives a restart.
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

    def _on_reset_layout(self) -> None:
        """Wipe persisted layout + snap any live floating widget back to
        its default geometry. Same effect as the Ctrl+Alt+R hotkey, just
        also reachable from this dialog so users who never learned the
        hotkey can recover from a misplaced drag."""
        from .. import layout as _layout
        from .floating_widget import FloatingWidget

        _layout.store().reset()
        for widget in FloatingWidget._instances:
            x, y = widget.DEFAULT_POS
            w, h = widget.DEFAULT_SIZE
            widget.setGeometry(x, y, w, h)
            widget.show()

    def _on_save(self) -> None:
        secrets.set_riot_api_key(self._riot_key.text().strip())
        secrets.set_riot_region(self._riot_region.currentText())
        secrets.set_llm_provider(self._llm_provider.currentData() or "openrouter")
        secrets.set_llm_api_key(self._llm_key.text().strip())
        # Keep legacy GROQ_API_KEY in sync if user is on groq, so existing
        # env-var users don't get surprised.
        if self._llm_provider.currentData() == "groq":
            secrets.set_groq_api_key(self._llm_key.text().strip())

        # Persist the Display checkboxes — read at startup by __main__,
        # so changes apply on next launch (we surface that fact in the
        # dialog's hint text rather than fake hot-reload).
        self._display_state.show_scoreboard = self._cb_scoreboard.isChecked()
        self._display_state.show_minimap_timers = self._cb_minimap.isChecked()
        self._display_state.show_lobby_stats = self._cb_lobby.isChecked()
        self._display_state.diagnostics_enabled = self._cb_diagnostics.isChecked()
        overlay_config.save(self._display_state)

        self.settings_changed.emit()
        self.accept()


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
    # Center the dialog on the cursor's screen.
    dlg = SettingsDialog(parent, hotkey_service=hotkey_service)
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        dlg.move(
            geo.center().x() - dlg.sizeHint().width() // 2,
            geo.center().y() - dlg.sizeHint().height() // 2,
        )
    result = dlg.exec()
    return result == QDialog.DialogCode.Accepted
