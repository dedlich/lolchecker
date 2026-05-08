"""View-model the overlay consumes.

Bundles everything the UI needs to render a single frame. Phase 6
(Integration) builds these from raw session payloads + advisor outputs;
Phase 5 (UI) only consumes them.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..advisor.ban_suggestions import BanSuggestion
from ..advisor.picks import PickSuggestion
from ..data.models import ChampionBuild, ChampSelectSession, CounterEntry, Role
from ..profiling.profile import EnemyProfile

ConnectionState = Literal["disconnected", "waiting", "connected", "reconnecting"]


class SessionView(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    connection_state: ConnectionState = "disconnected"
    session: ChampSelectSession | None = None
    # Indexed by enemy cell_id → counters in *that enemy's* role
    enemy_counters: dict[int, list[CounterEntry]] = Field(default_factory=dict)
    suggestions: list[PickSuggestion] = Field(default_factory=list)
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

    ally_damage_profile: dict[int, str] = Field(default_factory=dict)
    """Per-ally counterpart of ``enemy_damage_profile`` — drives the
    LiveCompanion ally-side "Damage Type" bar. Added in v1.10.85 to
    fix the 0% / 0% rendering caused by the prior empty-tag stub."""

    ally_phase_distribution: tuple[int, int, int] = (0, 0, 0)
    """``(early, mid, late)`` champion counts on the ally team, derived
    from the static tag heuristic in ``view_builder``. Drives the
    LiveCompanion power-spikes bar. Added in v1.10.90 — same kind of
    bug as the v1.10.85 ally damage one (UI was getting an empty
    ``tags_lookup`` stub)."""

    enemy_phase_distribution: tuple[int, int, int] = (0, 0, 0)
    """``(early, mid, late)`` champion counts on the enemy team. Same
    derivation as ``ally_phase_distribution``."""

    game_plan_enabled: bool = False
    """True iff a LLM API key is configured. The game-plan panel reads
    this to differentiate "in-flight, will arrive next snapshot" from
    "no LLM key, configure in Settings to enable game plans"."""

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

    my_champion_key: str = ""
    """Key of the local player's locked champion (e.g. 'Ahri'). Empty before lock-in."""

    my_champion_role: Role | None = None
    """Assigned role for the local player. None before role is set."""

    my_champion_build: ChampionBuild | None = None
    """Recommended build (runes/items/summoners/skill_order) for the locked champion.
    None when champion not yet locked or no build data available."""

    my_champion_phase: str = ""
    """Static-tag phase classification for the locked champion: ``"early"``
    / ``"mid"`` / ``"late"`` / ``""``. Drives the LiveCompanion right
    column's Champion Power Spikes one-liner. Empty before lock-in.
    Same heuristic as ``ally_phase_distribution`` — Early-Game /
    Lane-Bully → early, Late-Game / Hyper-Carry / Scaling → late, all
    others → mid. Added in v1.10.100."""

    game_plan_text: str = ""
    """LLM-generated game-plan paragraph for the locked champion in the
    confirmed matchup. Empty until a cached prose is available — populated
    from ``GamePlanLLMService.get_cached`` in ``_build_view``. The
    background prefetch fires on lock-in so this lights up on the
    snapshot AFTER the LLM responds, not the lock-in tick itself."""
