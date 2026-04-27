"""View-model the overlay consumes.

Bundles everything the UI needs to render a single frame. Phase 6
(Integration) builds these from raw session payloads + advisor outputs;
Phase 5 (UI) only consumes them.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..advisor.composition import CompositionGap
from ..advisor.picks import PickSuggestion
from ..data.models import ChampionBuild, ChampSelectSession, CounterEntry
from ..profiling.profile import EnemyProfile

ConnectionState = Literal["disconnected", "waiting", "connected", "reconnecting"]


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    connection_state: ConnectionState = "disconnected"
    session: ChampSelectSession | None = None
    # Indexed by enemy cell_id → counters in *that enemy's* role
    enemy_counters: dict[int, list[CounterEntry]] = Field(default_factory=dict)
    suggestions: list[PickSuggestion] = Field(default_factory=list)
    gaps: list[CompositionGap] = Field(default_factory=list)
    enemy_names: dict[int, str] = Field(default_factory=dict)
    """Map champion id → display name (filled from Data Dragon by integration)."""

    enemy_keys: dict[int, str] = Field(default_factory=dict)
    """Map champion id → string key (e.g. 86 → "Garen") for icon lookup."""

    enemy_roles: dict[int, str] = Field(default_factory=dict)
    """Map enemy cell_id → resolved role (override / tag-inference / cell-order).
    Used by the UI to render the role label and detect manual overrides."""

    enemy_role_overridden: set[int] = Field(default_factory=set)
    """cell_ids whose role comes from a manual user override (not auto)."""

    suggestion_builds: dict[str, ChampionBuild] = Field(default_factory=dict)
    """Map champion key → recommended build (runes/items/summoners) for the
    suggestions in this view. PickCard renders this when present."""

    enemy_profiles: dict[int, EnemyProfile] = Field(default_factory=dict)
    """Map enemy cell_id → fetched profile (mains, win-rate, streak) when
    a Riot API key is configured. Empty dict otherwise."""
