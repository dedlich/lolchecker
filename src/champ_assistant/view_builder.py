"""Pure view-building — translate (session, deps) → SessionView.

Extracted from ``app.py`` where ``ChampAssistant._build_view`` (115 LOC)
plus ten ``_compute_*`` / ``_resolve_*`` / ``_lookup_*`` helpers
(another ~280 LOC) lived inline. The orchestrator now just packages
its mutable state into a ``ViewBuilderDeps`` and calls
``build_session_view``.

Design decisions
----------------
* **One side-effect injection point** — ``deps.schedule_runtime_fetch``
  is a callable the orchestrator wires up. The view builder calls it
  when a Lolalytics fetch needs to start, but doesn't know how the
  scheduling actually happens (Coalescer + asyncio loop). Keeps the
  view builder oblivious to async machinery.
* **Profiles are read-only** — ``deps.enemy_profiles_by_cell`` /
  ``deps.ally_profiles_by_cell`` are passed in as dicts (not
  callbacks). The async fetch that populates them is owned by
  ``ChampAssistant._maybe_fetch_profiles``, which the orchestrator
  invokes alongside ``build_session_view``. Two responsibilities,
  cleanly separated.
* **No class** — pure functions with explicit ``deps`` parameter.
  Each ``_compute_*`` is independently testable by constructing a
  small ``ViewBuilderDeps`` and calling it directly.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .advisor.composition import CompositionGap, analyze_composition
from .advisor.counters import find_counters
from .advisor.picks import PickSuggestion, suggest_picks
from .advisor.role_inference import infer_role_from_tags, role_at_index
from .data.models import (
    BuildLibrary,
    ChampionBuild,
    ChampSelectSession,
    Champion,
    CounterEntry,
    CounterMatrix,
    Role,
    TagsData,
    TeamMember,
    TierList,
)
from .data.runtime_counters import RuntimeCounterStore
from .profiling.profile import EnemyProfile
from .ui.view_model import ConnectionState, SessionView


@dataclass(frozen=True)
class ViewBuilderDeps:
    """Static + per-tick state the view builder needs.

    Held by the orchestrator (``ChampAssistant``); rebuilt every tick
    so dict references stay current. ``schedule_runtime_fetch`` is the
    only callable — invoked when a counter lookup misses both the seed
    JSON and the runtime cache, telling the orchestrator to kick off a
    Lolalytics fetch.
    """
    connection_state: ConnectionState
    counters: CounterMatrix
    tiers: TierList
    tags: TagsData
    champions: dict[int, Champion]
    builds: BuildLibrary
    runtime_counters: RuntimeCounterStore | None
    enemy_role_overrides: dict[int, Role]
    enemy_profiles_by_cell: dict[int, EnemyProfile]
    ally_profiles_by_cell: dict[int, EnemyProfile]
    schedule_runtime_fetch: Callable[[str, Role], None]


# --------------------------------------------------------------------------
# Per-derivation helpers — each returns a piece of the final SessionView.
# Pure: no I/O, no Qt, no asyncio.
# --------------------------------------------------------------------------


def _team_keys(team: list[TeamMember], champions: dict[int, Champion]) -> list[str]:
    """Champion keys for every locked-in slot on ``team``."""
    keys: list[str] = []
    for member in team:
        if member.champion_id == 0:
            continue
        champ = champions.get(member.champion_id)
        if champ is not None:
            keys.append(champ.key)
    return keys


def _resolve_enemy_role(
    enemy: TeamMember,
    index: int,
    champion: Champion | None,
    enemy_role_overrides: dict[int, Role],
) -> Role | None:
    """Resolve an enemy slot's role with this priority:
    1. Manual override (user clicked the role label in the UI)
    2. assigned_position from the LCU (rare — usually empty for the enemy)
    3. Tag-based heuristic from the picked champion's Data Dragon tags
    4. Cell-order fallback (TOP/JUNGLE/MID/BOT/SUPPORT by index)
    """
    override = enemy_role_overrides.get(enemy.cell_id)
    if override is not None:
        return override
    if enemy.assigned_position is not None:
        return enemy.assigned_position
    if champion is not None:
        inferred = infer_role_from_tags(champion.tags)
        if inferred is not None:
            return inferred
    return role_at_index(index)


def _lookup_counters(
    enemy_key: str,
    role: Role,
    deps: ViewBuilderDeps,
) -> list[CounterEntry]:
    """Three-tier counter resolution:
      1. Seed JSON (deterministic, instant)
      2. Runtime cache (Groq response we already fetched)
      3. Fire-and-forget Groq fetch — view will re-render when it lands
    """
    seed = find_counters(enemy_key, role, deps.counters, limit=5)
    if seed:
        return seed
    if deps.runtime_counters is not None:
        cached = deps.runtime_counters.get_cached(enemy_key, role)
        if cached:
            return cached
        deps.schedule_runtime_fetch(enemy_key, role)
    return []


def _enriched_counters(
    enemy_keys: list[str], role: Role, deps: ViewBuilderDeps,
) -> CounterMatrix:
    """Return ``deps.counters`` merged with any cached Lolalytics data.

    For each revealed enemy, if the runtime store has a cached counter
    list for that matchup it overwrites the seed-JSON entry. Result is
    a one-shot CounterMatrix passed to ``suggest_picks`` so the
    fallback path benefits from Lolalytics data fetched in previous
    sessions — without firing any new network requests.
    """
    if deps.runtime_counters is None:
        return deps.counters
    merged: dict[str, dict] = {
        k: dict(v) for k, v in deps.counters.matrix.items()
    }
    changed = False
    for enemy_key in enemy_keys:
        cached = deps.runtime_counters.get_cached(enemy_key, role)
        if cached:
            merged.setdefault(enemy_key, {})[role] = cached
            changed = True
    if not changed:
        return deps.counters
    return CounterMatrix(matrix=merged)


def _enemy_in_role(
    session: ChampSelectSession,
    target_role: Role,
    deps: ViewBuilderDeps,
) -> Champion | None:
    """Find the enemy locked into ``target_role`` (if any)."""
    for i, enemy in enumerate(session.their_team):
        if enemy.champion_id == 0:
            continue
        champ = deps.champions.get(enemy.champion_id)
        if champ is None:
            continue
        role = _resolve_enemy_role(enemy, i, champ, deps.enemy_role_overrides)
        if role == target_role:
            return champ
    return None


def _suggestions_from_counters(
    counters: list[CounterEntry],
    *,
    lane_opponent_key: str,
    drafted: set[str],
    my_role: Role,
    gaps: list[CompositionGap],
    deps: ViewBuilderDeps,
) -> list[PickSuggestion]:
    """Convert raw counter list into scored PickSuggestions.

    Score combines:
      - counter strength (CounterEntry.score × 8, range ~0-80) — primary
      - tier bonus from tiers.json (S+:10, S:7, A:4, ...) — secondary
      - composition gap-fill (matches advisor.picks._GAP_FILL_BONUS) — tertiary
    Drafted champions excluded; result clamped to [0, 100].
    """
    from .advisor.picks import _GAP_FILL_BONUS, _GAP_TAGS, _TIER_SCORE

    out: list[PickSuggestion] = []
    for c in counters:
        if c.champion in drafted:
            continue
        counter_score = min(c.score * 8.0, 80.0)
        tier_score = _TIER_SCORE.get(c.tier or "", 0.0) * 0.5  # halved
        champ_tags = set(deps.tags.tags_for(c.champion))
        gap_score = 0.0
        gap_reasons: list[str] = []
        for gap in gaps:
            tags_for_gap = _GAP_TAGS.get(gap.category, set())
            if champ_tags & tags_for_gap:
                bonus = _GAP_FILL_BONUS.get(gap.severity, 0.0)
                gap_score += bonus
                gap_reasons.append(f"fills {gap.category}")

        total = max(0.0, min(100.0, counter_score + tier_score + gap_score))
        reasons = [f"Counters {lane_opponent_key} ({c.score:.1f})"]
        if c.tier:
            reasons.append(f"{c.tier} tier")
        reasons.extend(gap_reasons[:2])

        out.append(
            PickSuggestion(
                champion_key=c.champion,
                score=total,
                tier=c.tier,
                reasons=reasons,
            )
        )
    out.sort(key=lambda s: -s.score)
    return out


def _compute_enemy_counters(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, list]:
    result: dict[int, list] = {}
    for i, enemy in enumerate(session.their_team):
        if enemy.champion_id == 0:
            continue
        champ = deps.champions.get(enemy.champion_id)
        role = _resolve_enemy_role(enemy, i, champ, deps.enemy_role_overrides)
        if role is None or champ is None:
            continue
        counters = _lookup_counters(champ.key, role, deps)
        result[enemy.cell_id] = counters[:3]
    return result


def _compute_enemy_roles(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, Role]:
    """Resolved role per enemy cell — surfaces to the UI for the role label."""
    result: dict[int, Role] = {}
    for i, enemy in enumerate(session.their_team):
        champ = (
            deps.champions.get(enemy.champion_id)
            if enemy.champion_id else None
        )
        role = _resolve_enemy_role(enemy, i, champ, deps.enemy_role_overrides)
        if role is not None:
            result[enemy.cell_id] = role
    return result


def _compute_enemy_names(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, str]:
    names: dict[int, str] = {}
    for enemy in session.their_team:
        if enemy.champion_id == 0:
            continue
        champ = deps.champions.get(enemy.champion_id)
        if champ is not None:
            names[enemy.champion_id] = champ.name
    return names


def _compute_enemy_keys(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, str]:
    keys: dict[int, str] = {}
    for enemy in session.their_team:
        if enemy.champion_id == 0:
            continue
        champ = deps.champions.get(enemy.champion_id)
        if champ is not None:
            keys[enemy.champion_id] = champ.key
    return keys


def _compute_enemy_damage_profile(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, str]:
    """Per-enemy damage classification (AP / AD / AP/AD / "")
    keyed by cell_id. Drives the EnemyRow damage badge."""
    from .advisor.build_adapter import damage_profile_for_tags
    out: dict[int, str] = {}
    for enemy in session.their_team:
        if enemy.champion_id == 0:
            continue
        champ = deps.champions.get(enemy.champion_id)
        if champ is None:
            continue
        tags = deps.tags.tags_for(champ.key) or champ.tags
        out[enemy.cell_id] = damage_profile_for_tags(tags)
    return out


def _compute_ally_damage_profile(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> dict[int, str]:
    """Per-ally damage classification — same shape as the enemy version.

    Added in v1.10.85 so LiveCompanion's ally-side "Damage Type" bar
    can render without falling back to the empty ``_tags_for`` stub
    (which produced 0% / 0% in v1.10.78–v1.10.84)."""
    from .advisor.build_adapter import damage_profile_for_tags
    out: dict[int, str] = {}
    for ally in session.my_team:
        if ally.champion_id == 0:
            continue
        champ = deps.champions.get(ally.champion_id)
        if champ is None:
            continue
        tags = deps.tags.tags_for(champ.key) or champ.tags
        out[ally.cell_id] = damage_profile_for_tags(tags)
    return out


def _compute_picks(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> tuple[list[PickSuggestion], list[CompositionGap]]:
    me = session.me
    if me is None or me.assigned_position is None:
        return [], []

    my_role = me.assigned_position
    my_keys = _team_keys(session.my_team, deps.champions)
    enemy_keys = _team_keys(session.their_team, deps.champions)
    gaps = analyze_composition(my_keys, deps.tags)

    # If the lane opponent is locked in, prioritize counters specifically
    # against them — but still keep team-comp synergy in the score.
    lane_opponent = _enemy_in_role(session, my_role, deps)
    if lane_opponent is not None:
        counters = _lookup_counters(lane_opponent.key, my_role, deps)
        if counters:
            drafted = {k for k in (my_keys + enemy_keys) if k}
            lane_suggestions = _suggestions_from_counters(
                counters,
                lane_opponent_key=lane_opponent.key,
                drafted=drafted,
                my_role=my_role,
                gaps=gaps,
                deps=deps,
            )
            if lane_suggestions:
                return lane_suggestions[:5], gaps

    # Fallback: tier-based suggestions when no lane opponent yet OR
    # we have no counter data for them.
    enriched = _enriched_counters(enemy_keys, my_role, deps)
    suggestions = suggest_picks(
        my_role,
        my_keys,
        enemy_keys,
        gaps,
        deps.tiers,
        enriched,
        deps.tags,
        limit=5,
    )
    return suggestions, gaps


def _compute_picks_categorized(
    session: ChampSelectSession, deps: ViewBuilderDeps,
) -> tuple[list[PickSuggestion], list[PickSuggestion]]:
    """Return (counter_picks, synergy_picks) for the two-column pick panel.

    counter_picks: champions that beat the enemy lane opponent.
    synergy_picks: champions that fill team comp gaps (tier + gap-fill,
        no counter component so it complements rather than duplicates).
    """
    me = session.me
    if me is None or me.assigned_position is None:
        return [], []

    my_role = me.assigned_position
    my_keys = _team_keys(session.my_team, deps.champions)
    enemy_keys = _team_keys(session.their_team, deps.champions)
    gaps = analyze_composition(my_keys, deps.tags)

    counter_picks: list[PickSuggestion] = []
    lane_opponent = _enemy_in_role(session, my_role, deps)
    if lane_opponent is not None:
        counters = _lookup_counters(lane_opponent.key, my_role, deps)
        if counters:
            drafted = {k for k in (my_keys + enemy_keys) if k}
            raw = _suggestions_from_counters(
                counters,
                lane_opponent_key=lane_opponent.key,
                drafted=drafted,
                my_role=my_role,
                gaps=gaps,
                deps=deps,
            )
            counter_picks = raw[:5]

    # Synergy picks — tier + gap-fill only. Empty enemy_keys → no counter score.
    synergy_picks = suggest_picks(
        my_role,
        my_keys,
        [],
        gaps,
        deps.tiers,
        deps.counters,
        deps.tags,
        limit=5,
    )

    return counter_picks, synergy_picks


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def build_session_view(
    session: ChampSelectSession | None,
    deps: ViewBuilderDeps,
) -> SessionView:
    """Translate a parsed champ-select session + dependencies into the
    ``SessionView`` the UI overlay consumes. Pure — every dict / list in
    the result is independent of ``deps`` mutability after return.
    """
    if session is None:
        return SessionView(connection_state=deps.connection_state)

    enemy_counters = _compute_enemy_counters(session, deps)
    enemy_names = _compute_enemy_names(session, deps)
    enemy_keys = _compute_enemy_keys(session, deps)
    enemy_roles = _compute_enemy_roles(session, deps)
    enemy_damage_profile = _compute_enemy_damage_profile(session, deps)
    ally_damage_profile = _compute_ally_damage_profile(session, deps)
    suggestions, gaps = _compute_picks(session, deps)

    # Look up the recommended build for each suggestion in the local
    # player's role. Falls back to {} silently when builds aren't seeded.
    my_role: Role | None = None
    if session.me is not None:
        my_role = session.me.assigned_position
    suggestion_builds: dict[str, ChampionBuild] = {}
    suggestion_build_reasons: dict[str, list[str]] = {}
    if my_role is not None:
        from .advisor.build_adapter import adapt_build
        enemy_keys_list = _team_keys(session.their_team, deps.champions)
        for s in suggestions:
            base = deps.builds.build_for(s.champion_key, my_role)
            adapted = adapt_build(
                base, role=my_role,
                enemy_team_keys=enemy_keys_list,
                tags=deps.tags,
            )
            if adapted is None:
                continue
            suggestion_builds[s.champion_key] = adapted.build
            if adapted.reasons:
                suggestion_build_reasons[s.champion_key] = adapted.reasons

    from .advisor.ban_suggestions import suggest_bans
    ally_candidate_keys = (
        [s.champion_key for s in suggestions[:5]] if suggestions else []
    )
    _ban_kwargs = dict(
        session=session,
        champions=deps.champions,
        tiers=deps.tiers,
        enemy_profiles=deps.enemy_profiles_by_cell,
        counters=deps.counters,
        ally_candidate_keys=ally_candidate_keys,
    )
    ban_suggestions_lane = suggest_bans(
        **_ban_kwargs, my_role=my_role, limit=5,  # type: ignore[arg-type]
    )
    _lane_keys = {b.champion_key for b in ban_suggestions_lane}
    # Allround: global tier + mains, no role filter; exclude lane-ban dupes.
    _allround_raw = suggest_bans(
        **_ban_kwargs, my_role=None, limit=10,  # type: ignore[arg-type]
    )
    ban_suggestions_allround = [
        b for b in _allround_raw if b.champion_key not in _lane_keys
    ][:5]
    # Keep legacy field populated (union, capped at 3) so old tests still pass.
    bans = (
        ban_suggestions_lane
        + [b for b in ban_suggestions_allround if b not in ban_suggestions_lane]
    )[:3]

    picks_counter, picks_synergy = _compute_picks_categorized(session, deps)

    # My champion build — shown after the local player locks their pick.
    my_champion_key = ""
    my_champion_build = None
    me = session.me
    if me is not None and me.champion_id and me.champion_id in deps.champions:
        locked_champ = deps.champions[me.champion_id]
        my_champion_key = locked_champ.key
        if my_role is not None:
            from .advisor.build_adapter import adapt_build
            enemy_keys_list = _team_keys(session.their_team, deps.champions)
            base = deps.builds.build_for(locked_champ.key, my_role)
            adapted = adapt_build(
                base, role=my_role,
                enemy_team_keys=enemy_keys_list,
                tags=deps.tags,
            )
            if adapted is not None:
                my_champion_build = adapted.build
            elif base is not None:
                my_champion_build = base

    return SessionView(
        connection_state=deps.connection_state,
        session=session,
        enemy_counters=enemy_counters,
        suggestions=suggestions,
        gaps=gaps,
        enemy_names=enemy_names,
        enemy_keys=enemy_keys,
        all_champion_keys={c.id: c.key for c in deps.champions.values()},
        all_champion_names={c.id: c.name for c in deps.champions.values()},
        enemy_roles=enemy_roles,
        enemy_role_overridden=set(deps.enemy_role_overrides.keys()),
        enemy_damage_profile=enemy_damage_profile,
        ally_damage_profile=ally_damage_profile,
        suggestion_builds=suggestion_builds,
        suggestion_build_reasons=suggestion_build_reasons,
        enemy_profiles=dict(deps.enemy_profiles_by_cell),  # type: ignore[arg-type]
        ally_profiles=dict(deps.ally_profiles_by_cell),  # type: ignore[arg-type]
        ban_suggestions=bans,
        ban_suggestions_lane=ban_suggestions_lane,
        ban_suggestions_allround=ban_suggestions_allround,
        picks_counter=picks_counter,
        picks_synergy=picks_synergy,
        my_champion_key=my_champion_key,
        my_champion_role=my_role,
        my_champion_build=my_champion_build,
    )
