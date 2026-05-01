"""View-model the overlay consumes.

Bundles everything the UI needs to render a single frame. Phase 6
(Integration) builds these from raw session payloads + advisor outputs;
Phase 5 (UI) only consumes them.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..advisor.ban_suggestions import BanSuggestion
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

    all_champion_keys: dict[int, str] = Field(default_factory=dict)
    """Map champion id → string key for EVERY champion in the registry,
    not just the 5 enemy picks. Used by EnemyRow's mains-icon row —
    a player's main champions can be anything, not necessarily a
    member of the current lobby."""

    all_champion_names: dict[int, str] = Field(default_factory=dict)
    """Companion to ``all_champion_keys`` — id → display name."""

    enemy_roles: dict[int, str] = Field(default_factory=dict)
    """Map enemy cell_id → resolved role (override / tag-inference / cell-order).
    Used by the UI to render the role label and detect manual overrides."""

    enemy_damage_profile: dict[int, str] = Field(default_factory=dict)
    """Map enemy cell_id → ``"AP"`` / ``"AD"`` / ``"AP/AD"`` / ``""``.
    Surfaced by EnemyRow as a small badge so the player sees the team's
    damage mix at a glance and can prioritize MR vs Armor accordingly."""

    enemy_role_overridden: set[int] = Field(default_factory=set)
    """cell_ids whose role comes from a manual user override (not auto)."""

    suggestion_builds: dict[str, ChampionBuild] = Field(default_factory=dict)
    """Map champion key → recommended build (runes/items/summoners) for the
    suggestions in this view. PickCard renders this when present."""

    suggestion_build_reasons: dict[str, list[str]] = Field(default_factory=dict)
    """Map champion key → human-readable adaptation reasons when the
    base build was modified for the matchup (e.g. "vs AP-heavy: ...
    → Mercury's Treads"). Empty list means "no adaptation, build is
    role-default"."""

    enemy_profiles: dict[int, EnemyProfile] = Field(default_factory=dict)
    """Map enemy cell_id → fetched profile (mains, win-rate, streak) when
    a Riot API key is configured. Empty dict otherwise."""

    ally_profiles: dict[int, EnemyProfile] = Field(default_factory=dict)
    """Map ally cell_id → fetched profile. Same shape as enemy_profiles,
    just for the player's own team (excluding the local player). Used
    by the lobby panel during the post-lock-in / loading-screen window
    when all 10 players' picks are known."""

    ban_suggestions: list[BanSuggestion] = Field(default_factory=list)
    """Top-N champions to ban, ranked by tier + enemy mains."""

    ban_suggestions_lane: list[BanSuggestion] = Field(default_factory=list)
    """Top-5 ban suggestions targeted to the player's assigned lane."""

    ban_suggestions_allround: list[BanSuggestion] = Field(default_factory=list)
    """Top-5 ban suggestions based on global tier + enemy mains (no lane filter)."""

    picks_counter: list[PickSuggestion] = Field(default_factory=list)
    """Picks that counter the enemy lane opponent (empty when opponent not yet locked in)."""

    picks_synergy: list[PickSuggestion] = Field(default_factory=list)
    """Picks that fill team composition gaps (tier + gap-fill, no counter focus)."""
