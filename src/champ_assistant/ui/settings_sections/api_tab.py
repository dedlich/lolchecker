"""API Keys tab — Riot Web API + optional LLM provider."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from ... import secrets
from ...profiling.riot_api import PLATFORM_HOSTS
from .. import styles
from ._helpers import scrolling_page, section_header, vertical

if TYPE_CHECKING:
    from ..settings_dialog import SettingsDialog


def build_api_tab(dlg: "SettingsDialog") -> QWidget:
    """Riot Web API + optional LLM provider for live counter
    lookup. All keys go through ``secrets`` (OS keyring).

    Mutates ``dlg`` to set ``_riot_key``, ``_riot_region``,
    ``_llm_provider``, ``_llm_key``, ``_llm_help``."""
    page = scrolling_page()
    body = vertical(page)

    body.addWidget(section_header("Riot Web API"))
    riot_form = QFormLayout()
    riot_form.setHorizontalSpacing(12)
    riot_form.setVerticalSpacing(8)

    dlg._riot_key = QLineEdit()
    dlg._riot_key.setEchoMode(QLineEdit.EchoMode.Password)
    dlg._riot_key.setPlaceholderText("RGAPI-...")
    dlg._riot_key.setText(secrets.riot_api_key())
    riot_form.addRow("API Key", dlg._riot_key)

    dlg._riot_region = QComboBox()
    for region in PLATFORM_HOSTS:
        dlg._riot_region.addItem(region)
    idx = dlg._riot_region.findText(secrets.riot_region())
    if idx >= 0:
        dlg._riot_region.setCurrentIndex(idx)
    riot_form.addRow("Region", dlg._riot_region)
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

    body.addWidget(section_header("Live Counter Lookup (optional)"))
    llm_form = QFormLayout()
    llm_form.setHorizontalSpacing(12)
    llm_form.setVerticalSpacing(8)

    dlg._llm_provider = QComboBox()
    for label, value in (
        ("OpenRouter (empfohlen)", "openrouter"),
        ("Groq", "groq"),
        ("Google Gemini", "gemini"),
    ):
        dlg._llm_provider.addItem(label, value)
    idx = max(0, dlg._llm_provider.findData(secrets.llm_provider()))
    dlg._llm_provider.setCurrentIndex(idx)
    llm_form.addRow("Provider", dlg._llm_provider)

    dlg._llm_key = QLineEdit()
    dlg._llm_key.setEchoMode(QLineEdit.EchoMode.Password)
    dlg._llm_key.setPlaceholderText("API-Key")
    dlg._llm_key.setText(secrets.llm_api_key())
    llm_form.addRow("API Key", dlg._llm_key)
    body.addLayout(llm_form)

    dlg._llm_help = QLabel("")
    dlg._llm_help.setOpenExternalLinks(True)
    dlg._llm_help.setStyleSheet(
        f"color: {styles.TEXT_MUTED}; font-size: {styles.FS_LABEL}px;"
    )
    dlg._llm_help.setWordWrap(True)
    body.addWidget(dlg._llm_help)
    dlg._llm_provider.currentIndexChanged.connect(dlg._refresh_llm_help)
    dlg._refresh_llm_help()

    body.addStretch(1)
    return page
