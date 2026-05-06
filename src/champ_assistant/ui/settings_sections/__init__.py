"""Per-tab modules for the settings dialog.

Each ``build_*_tab(dlg)`` mutates the passed-in ``SettingsDialog`` —
it sets ``dlg._cb_*`` attributes (preserved for backwards compat with
``tests/ui/test_settings_dialog.py``) and returns the assembled
``QWidget`` to be added as a tab.

Helpers (``_scrolling_page``, ``_section_header``, ``_checkbox``, …)
live in ``_helpers.py``.
"""
