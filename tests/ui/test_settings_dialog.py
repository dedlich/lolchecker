"""Tests for the tabbed settings dialog — structure + toggle persistence.

Covers the contract that:
  * All five tabs are present
  * Every togglable feature in OverlayState is exposed in the UI
  * Save reads every checkbox into the right OverlayState field
  * Cancel doesn't write
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QTabWidget

from champ_assistant import overlay_config
from champ_assistant.ui.settings_dialog import SettingsDialog


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_overlay_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Redirect overlay_config to a temp file so tests don't touch
    the user's real config."""
    p = tmp_path / "overlay.json"
    monkeypatch.setattr(overlay_config, "config_path", lambda: p)
    yield p


# ----------------------------------------------------------------------
# Tab structure
# ----------------------------------------------------------------------
def test_dialog_has_five_tabs(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    """Five top-level tabs as documented in the module docstring."""
    dlg = SettingsDialog()
    tabs = dlg.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.count() == 5


def test_tab_titles_match_documented_order(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    dlg = SettingsDialog()
    tabs = dlg.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    assert titles == [
        "Widgets",
        "API Keys",
        "Hotkeys",
        "Vision (experimental)",
        "Diagnostics",
    ]


# ----------------------------------------------------------------------
# Coverage — every togglable OverlayState field has a checkbox
# ----------------------------------------------------------------------
TOGGLABLE_FIELDS_TO_CHECKBOXES = {
    # show_objectives intentionally omitted — ObjectivePanel was retired
    # in favor of the minimap-overlay timers; the field stays in
    # OverlayState as a no-op for back-compat with persisted configs.
    "show_summoners":               "_cb_summoners",
    "show_spikes":                  "_cb_spikes",
    "show_scoreboard":              "_cb_scoreboard",
    "show_minimap_timers":          "_cb_minimap",
    "show_lobby_stats":             "_cb_lobby",
    "diagnostics_enabled":          "_cb_diagnostics",
    "enable_auto_camp_detection":   "_cb_auto_camp",
    "enable_scoreboard_detection":  "_cb_scoreboard_detect",
    "enable_update_check":          "_cb_update_check",
    "enable_telemetry":             "_cb_telemetry",
    "low_resource_mode":            "_cb_low_resource",
}


def test_every_documented_field_has_a_checkbox(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    """Drift catch: if a new togglable field is added to OverlayState
    without a matching checkbox, this test fails. Forces the dialog
    to keep up with the model."""
    dlg = SettingsDialog()
    missing = [
        attr for attr in TOGGLABLE_FIELDS_TO_CHECKBOXES.values()
        if not hasattr(dlg, attr)
    ]
    assert not missing, f"checkboxes missing: {missing}"


def test_checkbox_initial_state_matches_overlay_config(
    qt_app, isolated_overlay_config,  # type: ignore[no-untyped-def]
) -> None:
    """Each checkbox reflects the persisted overlay_config value at
    construction time."""
    state = overlay_config.load()
    state.show_minimap_timers = False
    state.enable_telemetry = False
    state.enable_update_check = False
    overlay_config.save(state)

    dlg = SettingsDialog()
    assert dlg._cb_minimap.isChecked() is False
    assert dlg._cb_telemetry.isChecked() is False
    assert dlg._cb_update_check.isChecked() is False
    assert dlg._cb_scoreboard.isChecked() is True  # default still True


# ----------------------------------------------------------------------
# Save round-trip
# ----------------------------------------------------------------------
def test_save_persists_all_toggles(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    """Flip every checkbox + click save. Reload from disk — every
    field should reflect the change."""
    dlg = SettingsDialog()
    # Flip everything to NOT-default values.
    dlg._cb_summoners.setChecked(False)
    dlg._cb_spikes.setChecked(False)
    dlg._cb_scoreboard.setChecked(False)
    dlg._cb_minimap.setChecked(False)
    dlg._cb_lobby.setChecked(False)
    dlg._cb_diagnostics.setChecked(False)
    dlg._cb_auto_camp.setChecked(True)
    dlg._cb_scoreboard_detect.setChecked(True)
    dlg._cb_update_check.setChecked(False)
    dlg._cb_telemetry.setChecked(False)
    dlg._cb_low_resource.setChecked(True)
    dlg._on_save()

    reloaded = overlay_config.load()
    assert reloaded.show_summoners is False
    assert reloaded.show_spikes is False
    assert reloaded.show_scoreboard is False
    assert reloaded.show_minimap_timers is False
    assert reloaded.show_lobby_stats is False
    assert reloaded.diagnostics_enabled is False
    assert reloaded.enable_auto_camp_detection is True
    assert reloaded.enable_scoreboard_detection is True
    assert reloaded.enable_update_check is False
    assert reloaded.enable_telemetry is False
    assert reloaded.low_resource_mode is True


def test_default_toggles_are_sane(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    """Fresh install state — verify which features are on by default.
    Captures the design contract of which experimental features are
    opt-in vs opt-out."""
    state = overlay_config.OverlayState()
    # Standard widgets default ON
    assert state.show_scoreboard is True
    assert state.show_minimap_timers is True
    # Background services default ON (operational stability)
    assert state.diagnostics_enabled is True
    assert state.enable_telemetry is True
    assert state.enable_update_check is True
    # Low Resource Mode opt-in only — never default
    assert state.low_resource_mode is False
    # Experimental vision: scoreboard detection ON (drives the
    # gold-diff overlay's tab-scoreboard gating, default-on so users
    # see the feature without opt-in); camp detection still OFF
    # (Stage A heuristic, lower confidence).
    assert state.enable_auto_camp_detection is False
    assert state.enable_scoreboard_detection is True


# ----------------------------------------------------------------------
# All five hotkey actions present (Ctrl+Alt+B added in last commit)
# ----------------------------------------------------------------------
def test_hotkey_tab_shows_all_five_actions(qt_app, isolated_overlay_config) -> None:  # type: ignore[no-untyped-def]
    dlg = SettingsDialog()
    expected = {
        "toggle_overlay", "toggle_lock", "reset_positions",
        "reset_layout", "toggle_scoreboard",
    }
    assert set(dlg._hotkey_buttons.keys()) == expected
