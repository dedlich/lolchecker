"""Rule registry — assembles ``ALL_RULES`` from the domain modules under
``rules/`` and re-exports the legacy public surface.

Per OPTIMIZATION.md §3.2 the rule bodies were split out of this 3,930-LOC
module into eight domain files (objectives, combat, lane, bounty,
inhibitors, summoner_cd, personal, meta). What remains here:

  * Wildcard imports from ``_core`` / ``_state`` for the public
    Recommendation surface + reset helpers
  * Re-exports of constants + helpers that pre-split tests and call
    sites import directly from ``_rules``
  * The two rules that don't belong to any domain:
    - ``rule_game_ended``       — single structural endgame card
    - ``rule_situational_build`` — special two-arg signature, called
      separately by ``_evaluate.evaluate`` when a ``BuildResult`` is
      available
  * The ``ALL_RULES`` tuple itself (the registry every snapshot tick walks)
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...lcda.source import LcdaSnapshot

from ._core import *  # noqa: F401,F403 — Recommendation, constants, public helpers
from ._state import *  # noqa: F401,F403 — reset_*_hysteresis helpers
from ._core import (
    _CHAMP_DATA,
    _COMBAT_SPELLS,
    _DRAKE_DISPLAY,
    _active_ally_inhibitors_down,
    _active_enemy_inhibitors_down,
    _active_player,
    _alive_count,
    _ally_baron_buff_remaining,
    _ally_elder_buff_remaining,
    _ally_grub_count,
    _aoe_cc_warnings,
    _avg_level_diff,
    _drake_stack_count,
    _earliest_ally_inhib_respawn_remaining,
    _earliest_enemy_inhib_respawn_remaining,
    _enemy_baron_buff_remaining,
    _enemy_drake_stack_count,
    _enemy_elder_buff_remaining,
    _enemy_grub_count,
    _enemy_herald_pickup,
    _enemy_turrets_down,
    _fed_score,
    _focus_target,
    _herald_pickup,
    _is_jungler,
    _kill_streak,
    _objective_remaining,
    _parse_turret_name,
    _player_ids,
    _recent_ally_turret_losses,
    _team_gold_diff,
    _team_id_set,
    _team_kill_diff,
)
from ._state import (
    _ALLY_BOUNTY_HYSTERESIS,
    _BOUNTY_HYSTERESIS,
    _ENEMY_BOUNTY_HYSTERESIS,
    _FIRST_BLOOD_HYSTERESIS,
    _MATCHUP_MISMATCH_HYSTERESIS,
    _OBJECTIVE_BOUNTY_HYSTERESIS,
    _OBJECTIVE_TAKEN_HYSTERESIS,
    _PLATE_WINDOW_HYSTERESIS,
    _RECALL_HYSTERESIS,
    _SHUTDOWN_TAKEN_HYSTERESIS,
    _TEAMFIGHT_OUTCOME_HYSTERESIS,
)

# Domain rule modules — re-exported so legacy ``from ._rules import …``
# call sites + tests keep working without surgery on every test file.
from .rules.bounty import (  # noqa: F401
    OBJECTIVE_BOUNTY_DIFF_THRESHOLD,
    OBJECTIVE_BOUNTY_PHASE_END_S,
    OBJECTIVE_BOUNTY_PHASE_START_S,
    OBJECTIVE_BOUNTY_REARM_THRESHOLD,
    rule_active_bounty,
    rule_ally_bounty,
    rule_enemy_bounty,
    rule_objective_bounty_active,
)
from .rules.combat import (  # noqa: F401
    TEAMFIGHT_DECISIVE_NET,
    TEAMFIGHT_LOPSIDED_NET,
    TEAMFIGHT_MIN_TOTAL_KILLS,
    TEAMFIGHT_WINDOW_S,
    rule_ace_detected,
    rule_fight_opportunity,
    rule_fight_window_closing,
    rule_first_blood,
    rule_kill_deficit_defensive,
    rule_kill_lead_snowball,
    rule_numbers_advantage,
    rule_numbers_disadvantage,
    rule_shutdown_taken,
    rule_teamfight_outcome,
)
from .rules.inhibitors import (
    rule_ally_inhib_down,
    rule_ally_inhib_respawning,
    rule_enemy_base_exposed,
    rule_enemy_inhib_expiring,
    rule_enemy_inhibitor_down,
)
from .rules.lane import (  # noqa: F401
    CS_DEFICIT_TTL_S,
    CS_EXPECTED_PER_MIN,
    CS_INFO_DEFICIT,
    CS_LATE_SUPPRESS_S,
    CS_MIN_GAME_TIME_S,
    CS_WARN_DEFICIT,
    LANE_LEVEL_ADV_THRESHOLD,
    LANE_LEVEL_DOM_THRESHOLD,
    LANE_PHASE_CUTOFF_S,
    LANE_PHASE_EARLY_END_S,
    MISMATCH_DEFICIT_INFO,
    MISMATCH_DEFICIT_WARN,
    PLATE_WINDOW_CLOSE_S,
    PLATE_WINDOW_OPEN_S,
    _matchup_deficit,
    rule_ally_turret_lost,
    rule_cs_deficit,
    rule_gank_risk,
    rule_lane_level_advantage,
    rule_lane_opponent_mia,
    rule_lane_pressure,
    rule_matchup_mismatch,
    rule_plate_window,
)
from .rules.meta import (
    rule_far_behind_safe,
    rule_gold_lead_push,
    rule_late_game_group,
    rule_level_deficit,
)
from .rules.objectives import (  # noqa: F401
    OBJECTIVE_TAKEN_RECENT_S,
    SETUP_WINDOW_MAX_S,
    SETUP_WINDOW_MIN_S,
    rule_ally_herald_window,
    rule_baron_buff_expiring,
    rule_baron_give_up,
    rule_baron_priority,
    rule_baron_window,
    rule_drake_give_up,
    rule_drake_priority,
    rule_dragon_soul_pressure,
    rule_dragon_window,
    rule_elder_buff_expiring,
    rule_elder_window,
    rule_enemy_baron_buff,
    rule_enemy_dragon_soul,
    rule_enemy_elder_buff,
    rule_enemy_herald_danger,
    rule_enemy_jungler_down,
    rule_herald_priority,
    rule_objective_setup_window,
    rule_objective_taken_by_ally,
    rule_void_grubs,
)
from .rules.personal import (  # noqa: F401
    GOLD_BACK_WORTH,
    GOLD_COMPONENT_SPIKE,
    GOLD_LARGE_SPIKE,
    GOLD_RECALL_REARM_BUFFER,
    HP_CRITICAL_PCT,
    HP_LOW_PCT,
    HP_RECALL_REARM_PCT,
    MANA_DEPLETED_PCT,
    MANA_LOW_PCT,
    MANA_RECALL_REARM_PCT,
    RECALL_PHASE_END_S,
    SKILL_POINT_GAME_TIME_MIN_S,
    SKILL_POINT_HP_GATE_PCT,
    rule_enemy_item_spike,
    rule_power_spike,
    rule_recall_check,
    rule_tilt_detection,
    rule_unspent_skill_points,
)
# Summoner-cooldown rules — NOT in ``ALL_RULES``; ``_evaluate.evaluate``
# calls them in a separate loop when ``spell_tracker is not None``.
from .rules.summoner_cd import (
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
)


def rule_game_ended(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a final Win/Loss card when the GameEnd event is present.

    Shows ally drake count and final gold diff as context. Once this fires,
    the suppression pass drops all other recommendations — the game is over.
    """
    result = getattr(snapshot, "game_result", "") or ""
    if not result:
        return None
    ally_drakes = _drake_stack_count(snapshot)
    enemy_drakes = _enemy_drake_stack_count(snapshot)
    gold = _team_gold_diff(snapshot)
    drake_str = f"{ally_drakes}x Drake" if ally_drakes else "0 Drakes"
    if result == "Win":
        return Recommendation(
            text=f"SIEG! GG — {drake_str}, Gold {gold:+d}",
            severity="alert",
            category="tempo",
            confidence=1.0,
            risk="LOW",
            ttl_s=300.0,
            kind="game_end",
            reasons=(
                "VICTORY — Spiel gewonnen!",
                f"Ally Drakes: {ally_drakes} | Enemy Drakes: {enemy_drakes}",
                f"Final Gold-Diff: {gold:+d}",
            ),
        )
    return Recommendation(
        text=f"NIEDERLAGE — GG, nächstes Spiel ({drake_str})",
        severity="warn",
        category="safety",
        confidence=1.0,
        risk="LOW",
        ttl_s=300.0,
        kind="game_end",
        reasons=(
            "DEFEAT — Spiel verloren",
            f"Ally Drakes: {ally_drakes} | Enemy Drakes: {enemy_drakes}",
            f"Final Gold-Diff: {gold:+d}",
        ),
    )


# Rule registry — extend by appending a function. Order doesn't affect
# ``evaluate``'s output (caller sorts by severity).
ALL_RULES: tuple[Callable[["LcdaSnapshot"], Recommendation | None], ...] = (
    # Game-end summary — trumps everything when the match is over.
    rule_game_ended,
    # Ace detection — highest-priority window, overrides most other calls.
    rule_ace_detected,
    # Closing window — enemy about to respawn, finish the push.
    rule_fight_window_closing,
    # Power spike — ult ready / item completed; brief action window.
    rule_power_spike,
    # Enemy item spike — enemy carry just completed a legendary.
    rule_enemy_item_spike,
    # B2 gank window — enemy jungler MIA during laning phase.
    rule_gank_risk,
    # B2 lane-opponent MIA — your direct lane opponent absent from CS.
    rule_lane_opponent_mia,
    # B3 objective setup window — pre-spawn drake/baron/herald coaching.
    rule_objective_setup_window,
    # B5 skill-point nag — single most-missed micro-action.
    rule_unspent_skill_points,
    # B5 bounty awareness — proactive "you have a target on you" coaching.
    rule_active_bounty,
    # B5 enemy bounty — proactive "kill the fed carrier" focus call.
    rule_enemy_bounty,
    # B5 ally bounty — proactive "protect the fed ally" coaching.
    rule_ally_bounty,
    # B5 matchup mismatch — per-enemy deficit coaching ("lane lost").
    rule_matchup_mismatch,
    # B3 plate window — once-per-game reminder before plates despawn at 14:00.
    rule_plate_window,
    # B5 first blood — single-fire momentum / safety call after first kill.
    rule_first_blood,
    # B5 teamfight outcome — post-fight conversion / recovery coaching.
    rule_teamfight_outcome,
    # B5 shutdown taken — convert bountied-enemy death into map state.
    rule_shutdown_taken,
    # B5 objective taken — at-moment-of-kill conversion call.
    rule_objective_taken_by_ally,
    # B5 objective bounty — catch-up mechanic awareness (two-sided).
    rule_objective_bounty_active,
    # B4 tilt detection — active player's death pattern coaching.
    rule_tilt_detection,
    # B5 recall window — HP/mana/gold-driven back timing.
    rule_recall_check,
    # Numbers-asymmetry — safety overrides objective calls.
    rule_numbers_disadvantage,
    rule_numbers_advantage,
    # Pro-level window rules (replace the simpler drake/baron 4-pack).
    rule_elder_window,
    rule_dragon_window,
    rule_baron_window,
    rule_herald_priority,
    rule_fight_opportunity,
    # General tempo + safety rules.
    rule_gold_lead_push,
    rule_far_behind_safe,
    rule_level_deficit,
    rule_lane_level_advantage,
    rule_kill_lead_snowball,
    rule_kill_deficit_defensive,
    rule_cs_deficit,
    rule_late_game_group,
    # Post-soul pressure — fires for 2 minutes after securing Dragon Soul.
    rule_dragon_soul_pressure,
    # Early-game Void Grub objective (4:30–14:00 window).
    rule_void_grubs,
    # B2 contribution — enemy jungler is dead, push/contest window.
    rule_enemy_jungler_down,
    # B3 — enemy at soul point (3 drakes), persistent denial reminder.
    rule_enemy_dragon_soul,
    # Lane/base pressure — structural map-state (turrets + inhibs + herald).
    rule_enemy_herald_danger,
    rule_ally_herald_window,
    rule_enemy_inhibitor_down,
    rule_enemy_inhib_expiring,
    rule_ally_turret_lost,
    rule_ally_inhib_respawning,
    rule_ally_inhib_down,
    rule_baron_buff_expiring,
    rule_enemy_baron_buff,
    rule_enemy_elder_buff,
    rule_elder_buff_expiring,
    rule_enemy_base_exposed,
    rule_lane_pressure,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


def rule_situational_build(
    snapshot: "LcdaSnapshot",
    build_result: object,
) -> Recommendation | None:
    """Recommend situational items based on game state and enemy team comp.

    Fires after the first 2 minutes when enemy champions are confirmed.
    ``build_result`` is a ``BuildResult`` from the build engine; passed in
    by ``evaluate`` so the rule stays pure — no async I/O.

    Suppressed early game and when no situational items are computed.
    """
    from ..build_engine import BuildResult  # local import avoids circular

    if not isinstance(build_result, BuildResult):
        return None

    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < 120.0:
        return None

    situational = build_result.situational_items
    if not situational:
        return None

    top = situational[:3]

    context_lines = [
        r for s in top
        for r in s.reasons
        if any(kw in r for kw in ("Gegner", "Sustain", "Tank", "Penetration", "Golddefizit"))
    ]

    item_list = " / ".join(s.item_name for s in top)
    if context_lines:
        headline = context_lines[0]
        text = f"Situational: {item_list} — {headline}"
    else:
        text = f"Situational Items: {item_list}"

    all_reasons = tuple(
        r for s in top for r in s.reasons[:2]
    )

    return Recommendation(
        text=text,
        severity="info",
        category="lane",
        confidence=0.80,
        risk="LOW",
        ttl_s=120.0,
        kind="situational_build",
        reasons=all_reasons,
    )
