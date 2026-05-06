"""Settings dialog — assembles per-tab modules from ``settings_sections/``.

Tabs (in order shown to the user):

  * Widgets       — main panel sections + floating widget visibility
                    + reset-layout action
  * API Keys      — Riot Web API + optional LLM provider
  * Hotkeys       — six global hotkey bindings, click-to-rebind
  * Vision (Exp)  — experimental Windows-only color-heuristic
                    detectors (camp clearing + scoreboard visibility)
  * Diagnostics   — diagnostics logging, telemetry, update checks

Persistence:
  API keys + LLM key go through ``secrets`` (OS keyring).
  Everything else lives in ``overlay_config.OverlayState``.
  Hotkeys mirror to ``hotkey_config.HotkeyConfig``.

Per OPTIMIZATION.md §3.5 — the body of each tab lives in its own
``settings_sections/<tab>.py`` module so a 665-line file doesn't grow
with every new toggle. Each module's ``build_*_tab(dlg)`` mutates the
dialog (sets ``dlg._cb_*`` etc.) and returns the assembled QWidget.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTabWidget,
    QVBoxLayout,
)

from .. import hotkey_config, overlay_config, secrets
from . import styles
from .settings_sections._helpers import tab_widget_stylesheet
from .settings_sections.api_tab import build_api_tab
from .settings_sections.diagnostics_tab import build_diagnostics_tab
from .settings_sections.hotkeys_tab import build_hotkeys_tab
from .settings_sections.vision_tab import build_vision_tab
from .settings_sections.widgets_tab import build_widgets_tab


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
        self.setStyleSheet(
            f"QDialog {{ background-color: {styles.BG_PRIMARY}; }}"
        )

        # Persisted overlay state — every checkbox/toggle widget binds
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
        tabs.setStyleSheet(tab_widget_stylesheet())
        tabs.addTab(build_widgets_tab(self),     "Widgets")
        tabs.addTab(build_api_tab(self),         "API Keys")
        tabs.addTab(build_hotkeys_tab(self),     "Hotkeys")
        tabs.addTab(build_vision_tab(self),      "Vision (experimental)")
        tabs.addTab(build_diagnostics_tab(self), "Diagnostics")
        outer.addWidget(tabs, 1)

        outer.addWidget(self._build_button_row())

    # ------------------------------------------------------------------
    # Save / cancel / reset wiring
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
        # Secrets (keyring) — sourced from the API tab.
        secrets.set_riot_api_key(self._riot_key.text().strip())
        secrets.set_riot_region(self._riot_region.currentText())
        secrets.set_llm_provider(self._llm_provider.currentData() or "openrouter")
        secrets.set_llm_api_key(self._llm_key.text().strip())
        if self._llm_provider.currentData() == "groq":
            secrets.set_groq_api_key(self._llm_key.text().strip())

        # Overlay state — single dataclass, single save call.
        s = self._display_state
        s.show_summoners              = self._cb_summoners.isChecked()
        s.show_spikes                 = self._cb_spikes.isChecked()
        s.show_scoreboard             = self._cb_scoreboard.isChecked()
        s.show_minimap_timers         = self._cb_minimap.isChecked()
        s.show_lobby_stats            = self._cb_lobby.isChecked()
        s.diagnostics_enabled         = self._cb_diagnostics.isChecked()
        s.enable_auto_camp_detection  = self._cb_auto_camp.isChecked()
        s.enable_scoreboard_detection = self._cb_scoreboard_detect.isChecked()
        s.enable_update_check         = self._cb_update_check.isChecked()
        s.enable_telemetry            = self._cb_telemetry.isChecked()
        s.low_resource_mode           = self._cb_low_resource.isChecked()
        s.focus_mode                  = self._cb_focus.isChecked()
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
    # Hotkey capture flow (called by hotkeys_tab buttons)
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
