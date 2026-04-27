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
        self.setMinimumWidth(420)
        self.setStyleSheet(
            f"QDialog {{ background-color: {styles.BG_PRIMARY}; }}"
            f" QLabel {{ color: {styles.TEXT_PRIMARY};"
            f" font-family: {styles.FONT_FAMILY}; }}"
            f" QLineEdit, QComboBox {{ background-color: {styles.BG_SECONDARY};"
            f" color: {styles.TEXT_PRIMARY}; border: 1px solid {styles.BORDER};"
            f" border-radius: {styles.RADIUS_SMALL}px; padding: 4px 6px; }}"
            f" QLineEdit:focus, QComboBox:focus {{ border-color: {styles.ACCENT}; }}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        outer.addWidget(title)

        # -- Riot API -----------------------------------------------------
        riot_section = QLabel("Riot Web API")
        riot_section.setObjectName("sectionTitle")
        riot_section.setStyleSheet(
            f"color: {styles.TEXT_MUTED}; font-size: 11px; font-weight: 700;"
            " text-transform: uppercase; letter-spacing: 0.8px;"
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

        # -- Buttons ------------------------------------------------------
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        for btn in buttons.buttons():
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {styles.BG_SECONDARY};"
                f" color: {styles.TEXT_PRIMARY};"
                f" border: 1px solid {styles.BORDER};"
                f" border-radius: {styles.RADIUS_SMALL}px;"
                f" padding: 4px 14px; }}"
                f" QPushButton:hover {{ background-color: {styles.BG_ELEVATED}; }}"
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
