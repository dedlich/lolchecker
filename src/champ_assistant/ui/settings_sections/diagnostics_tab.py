"""Diagnostics tab — low-resource mode, focus mode, logging, telemetry, updates."""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

from ._helpers import checkbox, hint_label, scrolling_page, section_header, vertical

if TYPE_CHECKING:
    from ..settings_dialog import SettingsDialog


def build_diagnostics_tab(dlg: "SettingsDialog") -> QWidget:
    """Mutates ``dlg`` to set ``_cb_low_resource``, ``_cb_focus``,
    ``_cb_diagnostics``, ``_cb_telemetry``, ``_cb_update_check``."""
    page = scrolling_page()
    body = vertical(page)

    body.addWidget(section_header("Low Resource Mode"))
    dlg._cb_low_resource = checkbox(
        "Low Resource Mode (für schwächere Rechner / Streaming)",
        dlg._display_state.low_resource_mode,
    )
    body.addWidget(dlg._cb_low_resource)
    body.addWidget(hint_label(
        "Master-Switch: deaktiviert Vision-Detection, Telemetry, "
        "Update-Check und cap't Render-Rate auf 10 FPS. Andere "
        "Toggle-Settings bleiben gespeichert; LRM überschreibt "
        "sie nur zur Laufzeit."
    ))

    body.addWidget(section_header("Focus Mode"))
    dlg._cb_focus = checkbox(
        "Focus Mode (nur Top-1 Empfehlung, weniger Cognitive Load)",
        dlg._display_state.focus_mode,
    )
    body.addWidget(dlg._cb_focus)
    body.addWidget(hint_label(
        "Statt Top-3 Recommendations rendert das Panel nur die "
        "wichtigste. Hilft im stress-light wenn man eh nur EINE "
        "Sache anzeigen will."
    ))

    body.addWidget(section_header("Logging"))
    dlg._cb_diagnostics = checkbox(
        "Diagnose-Logging (CPU / Speicher / FPS alle 10s)",
        dlg._display_state.diagnostics_enabled,
    )
    body.addWidget(dlg._cb_diagnostics)
    body.addWidget(hint_label(
        "Schreibt in app.log neben dem Hauptlog. "
        "Hilft bei Performance-Auditing."
    ))

    body.addWidget(section_header("Telemetry"))
    dlg._cb_telemetry = checkbox(
        "Telemetrie-Aufzeichnung (lokal, append-only JSONL)",
        dlg._display_state.enable_telemetry,
    )
    body.addWidget(dlg._cb_telemetry)
    body.addWidget(hint_label(
        "Lokale Event-Logs für UX-Auswertung. "
        "Keine Netzwerk-Übertragung, keine User-Inputs / Chat / "
        "Account-Daten. Speicherort: telemetry.jsonl im "
        "Config-Verzeichnis."
    ))

    body.addWidget(section_header("Updates"))
    dlg._cb_update_check = checkbox(
        "Update-Check beim Start (GitHub Releases)",
        dlg._display_state.enable_update_check,
    )
    body.addWidget(dlg._cb_update_check)
    body.addWidget(hint_label(
        "Einmaliger HTTPS-Call gegen api.github.com beim "
        "App-Start. Aus für Metered Connections / "
        "Privacy-Setups."
    ))
    body.addStretch(1)
    return page
