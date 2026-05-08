"""Sub-widgets composed into ``ui/live_companion_view.LiveCompanionView``.

Mirrors the ``ui/settings_sections/`` split so each domain widget gets its
own file rather than swelling the main view module.
"""
from __future__ import annotations

from .bans_column import BansColumn
from .picks_column import PicksColumn
from .roster_panel import RosterPanel

__all__ = ["BansColumn", "PicksColumn", "RosterPanel"]
