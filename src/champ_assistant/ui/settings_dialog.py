"""Settings dialog for API keys + region.

Persists via ``champ_assistant.secrets`` (keyring-backed) so the same
keys carry over between launches without leaving plaintext on disk.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from .. import secrets
from ..profiling.riot_api import PLATFORM_HOSTS
from . import styles


class SettingsDialog(QDialog):
    """Modal settings dialog. Emits ``settings_changed`` on accept."""

    settings_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
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
            'Hol einen Dev-Key auf <a style="color:#5BA8FF;"'
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
            f'<a style="color:#5BA8FF;" href="{url}">{url}</a> '
            "und speichere ihn hier. Ohne Key benutzt der Assistant nur die"
            " mitgelieferten Counter-Daten."
        )

    def _on_save(self) -> None:
        secrets.set_riot_api_key(self._riot_key.text().strip())
        secrets.set_riot_region(self._riot_region.currentText())
        secrets.set_llm_provider(self._llm_provider.currentData() or "openrouter")
        secrets.set_llm_api_key(self._llm_key.text().strip())
        # Keep legacy GROQ_API_KEY in sync if user is on groq, so existing
        # env-var users don't get surprised.
        if self._llm_provider.currentData() == "groq":
            secrets.set_groq_api_key(self._llm_key.text().strip())
        self.settings_changed.emit()
        self.accept()


def open_settings(parent=None) -> bool:  # type: ignore[no-untyped-def]
    """Helper used by the title bar gear button. Returns True on save."""
    # Center the dialog on the cursor's screen.
    dlg = SettingsDialog(parent)
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        dlg.move(
            geo.center().x() - dlg.sizeHint().width() // 2,
            geo.center().y() - dlg.sizeHint().height() // 2,
        )
    result = dlg.exec()
    return result == QDialog.DialogCode.Accepted
