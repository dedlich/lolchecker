"""Top-level engine driver — severity sort + suppression + evaluate().

Imported through the package's ``__init__`` as the public ``evaluate``
function plus the ``_suppress_dominated`` helper that tests exercise
directly.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...lcda.source import LcdaSnapshot
    from ...lcda.spell_tracker import SpellTracker

from ._core import Recommendation
from ._rules import (
    ALL_RULES,
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
    rule_situational_build,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}
def _suppress_dominated(recs: list[Recommendation]) -> list[Recommendation]:
    """Remove recommendations made redundant by more specific ones.

    Suppression rules (applied in order):

    1. ace present → drop numbers_adv, fight, gold_lead, kill_lead (all
       subsumed by the ACE push signal). Keep safety and lane_open.

    2. numbers_disadv present → drop ALL offensive calls (fight, push,
       numbers_adv, and objective "take" recs). Keep give-up and safety.
       A teammate is dead — no aggressive rec should reach the user.

    3. dragon_free / baron_free already embeds the numbers-advantage
       signal in richer context → remove the standalone numbers_adv card.

    4. fight rec present → remove gold_lead and kill_lead (they are
       sub-signals of the same "you're ahead, press it" message).

    5. "don't fight" contradicts an active objective-take call;
       suppress fight_bad when we're already recommending taking an objective.
    """
    kinds = {r.kind for r in recs}

    # Rule 0 — game_end present: show only the result card, suppress everything
    if "game_end" in kinds:
        return [r for r in recs if r.kind == "game_end"]

    # Rule 1 — ace absorbs redundant offensive signals
    if "ace" in kinds:
        _ace_drop = {
            "fight", "fight_bad", "numbers_adv", "gold_lead", "kill_lead",
            "jungler_down",
            "window_closing",  # ace already urges the push — closing window is redundant
            "gank_risk",       # full-team push moment — laning gank caution is irrelevant
            "tilt",            # ace push moment dominates personal-tilt coaching
            "recall_resource", # 4v0 push moment — back can wait until after the play
            "recall_gold",     # ditto — don't recall through an ace window
            "mana_check",      # mana doesn't matter when team is doing the work
            "lane_mia",        # team-push beats laning push; group, don't side-lane
            "objective_setup", # ace push moment dominates objective prep
            "skill_point_unspent",  # micro-nag — irrelevant during ace push
            "active_bounty",        # bounty doesn't matter when team is winning the play
            "enemy_bounty",         # 5v0 push moment — focus call is implicit
            "ally_bounty",          # protect-the-carry irrelevant — push winning
            "matchup_mismatch",     # lane analysis irrelevant during ace push
            "plate_window",         # plates moot — push the win, not a tower trade
            "first_blood",          # FB momentum coaching irrelevant during ace push
            "teamfight_won",        # ace IS the conversion call — redundant
            "teamfight_won_big",
            "shutdown_taken",       # ace already includes the gold + map-state windfall
            # objective_taken_* — ace IS the conversion call; the buff windfall is implicit
            "objective_taken_baron",
            "objective_taken_elder",
            "objective_taken_soul",
            "objective_taken_drake",
            "objective_taken_herald",
            "objective_bounty_behind",  # already in winning push
            "objective_bounty_ahead",
        }
        recs = [r for r in recs if r.kind not in _ace_drop]
        kinds = {r.kind for r in recs}

    # Rule 2 — safety first
    if "numbers_disadv" in kinds:
        _offensive = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free",
            "elder_take",        # Elder is still a take — don't go while short-handed
            "flash_down",        # engage window irrelevant when short-handed
            "tp_down",           # side-lane freedom irrelevant when short-handed
            "combat_spell_down", # trade windows irrelevant when short-handed
            "baron_buff_expiring",  # pushing while outnumbered is still bad
            "elder_buff_expiring",  # same — don't fight when short-handed
            "ally_herald",       # don't split to place herald while down
            "inhib_expiring",    # don't push their base while short-handed
            "dragon_soul",       # don't Baron/Elder rush while short-handed
            "ally_hornguard",    # tower push irrelevant when down a player
            "void_grub_contest", # contesting grubs while short-handed is bad
            "jungler_down",      # push suggestion irrelevant when short-handed
            "enemy_soul_point",  # drake denial requires going aggro — not when short-handed
            "power_spike",       # "fight now!" irrelevant when team is short-handed
            "cs_deficit",        # farming advice irrelevant while team is down
            "lane_level_adv",    # lane trades irrelevant when short-handed
            "lane_mia",          # don't tell a short-handed player to side-push alone
            "objective_setup",   # don't push objectives short-handed — wait for ally
            "skill_point_unspent", # micro-nag drowns out the safety call
            "active_bounty",       # numbers_disadv is the more-urgent safety call
            "enemy_bounty",        # short-handed teams don't pick fights — focus is moot
            "ally_bounty",         # the more-urgent "play safe" already covers "don't 4v5"
            "matchup_mismatch",    # lane analysis drowns out the urgent safety call
            "plate_window",        # don't tell short-handed players to push for plates
            "first_blood",         # the more-urgent "play safe" already dominates
            "teamfight_won",       # can't claim "we won" while short-handed
            "teamfight_won_big",
            "shutdown_taken",      # don't tell short-handed players to push for objectives
            # teamfight_lost / teamfight_lost_big SURVIVE — they explain
            # WHY we're short-handed and the "no engage" message is valuable.
        }
        return [r for r in recs if r.kind not in _offensive]

    # Rule 3 — free-window objective absorbs standalone numbers_adv
    if "dragon_free" in kinds or "baron_free" in kinds or "elder_take" in kinds:
        recs = [r for r in recs if r.kind != "numbers_adv"]

    # Rule 4 — fight rec subsumes generic lead signals
    if "fight" in kinds:
        recs = [r for r in recs if r.kind not in {"gold_lead", "kill_lead"}]

    # Rule 5 — "don't fight" contradicts an active objective-take call;
    # suppress fight_bad when we're already recommending taking an objective.
    _obj_take = {"dragon_take", "dragon_free", "baron_take", "baron_free", "elder_take"}
    if kinds & _obj_take:
        recs = [r for r in recs if r.kind != "fight_bad"]

    # Rule 6 — base_exposed absorbs lane_open for the same lane context;
    # suppress generic lane_open cards when a base-exposure alert is present.
    if "base_exposed" in kinds:
        recs = [r for r in recs if r.kind != "lane_open"]

    # Rule 7 — inhib_down (building destroyed) supersedes base_exposed
    # (turret just fell). The state has advanced past base_exposed.
    if "inhib_down" in kinds:
        recs = [r for r in recs if r.kind not in {"base_exposed", "lane_open"}]

    # Rule 8 — ally_inhib_down is a defensive alert; suppress mid-map objective
    # "take" signals, inhib_expiring push, and the turret alert (inhib is worse).
    if "ally_inhib_down" in kinds:
        _obj_take_kinds = {
            "dragon_take", "baron_take", "elder_take",
            "lane_open", "inhib_expiring", "ally_turret_lost",
            "dragon_soul", "ally_hornguard", "void_grub_contest",
            "jungler_down",      # don't push while defending super-minions
            "enemy_soul_point",  # drake denial requires committing — not when base is open
            "cs_deficit",        # farming advice irrelevant while defending base
            "lane_level_adv",    # lane-trade window irrelevant while base is open
            "gank_risk",         # laning gank warning irrelevant when base is open
            "lane_mia",          # don't tell defenders to chase tempo in lane
            "objective_setup",   # ditto — defending base trumps objective prep
            "skill_point_unspent", # micro-nag drowns out the defense call
            "active_bounty",       # bounty coaching irrelevant when defending base
            "enemy_bounty",        # focus call irrelevant — they're inside your base
            "ally_bounty",         # protect-the-carry irrelevant — defend, don't enable
            "matchup_mismatch",    # lane analysis irrelevant — base is open
            "plate_window",        # plates moot when defending your own base
            "teamfight_won",       # post-fight tempo irrelevant — defend, not push
            "teamfight_won_big",
            "teamfight_lost",      # ditto — the urgent "defend base" call wins
            "teamfight_lost_big",
            "shutdown_taken",      # objective conversion irrelevant — defend base
            # All objective_taken_* — base defense trumps post-objective conversion
            "objective_taken_baron",
            "objective_taken_elder",
            "objective_taken_soul",
            "objective_taken_drake",
            "objective_taken_herald",
            "objective_bounty_behind",  # base defense trumps comeback push
            "objective_bounty_ahead",
            # NOTE: ally_inhib_respawning intentionally coexists with ally_inhib_down:
            # "defend now + inhib back in 30s" are complementary, not conflicting.
        }
        recs = [r for r in recs if r.kind not in _obj_take_kinds]

    # Rule 9 — cross-objective priority: when multiple objective-take kinds
    # fire simultaneously, keep only the highest-priority one.
    # Priority order: elder_take > baron_take/baron_free > dragon_take/dragon_free.
    kinds = {r.kind for r in recs}
    _present_baron = kinds & {"baron_take", "baron_free"}
    _present_dragon = kinds & {"dragon_take", "dragon_free"}
    if _present_baron and _present_dragon:
        # Baron beats dragon — drop the dragon objective cards.
        recs = [r for r in recs if r.kind not in _present_dragon]
        kinds = {r.kind for r in recs}

    # Rule 10 — enemy Elder buff is active: suppress all "fight/push" signals.
    # Fighting while enemy has Elder execute is nearly always fatal.
    if "enemy_elder_buff" in kinds:
        _fight_kinds = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "dragon_take", "dragon_free", "baron_take", "baron_free", "elder_take",
            "baron_buff_expiring", "elder_buff_expiring",
            "dragon_soul", "jungler_down",
            "objective_setup",  # don't prep objectives — they'll execute you
            "enemy_bounty",     # focus call moot when fighting equals death
            "ally_bounty",      # protect-the-carry moot when entire team must back
        }
        recs = [r for r in recs if r.kind not in _fight_kinds]

    # Rule 10b — far_behind_safe ("scale, no fights") contradicts
    # objective_bounty_behind ("force objectives for bounty gold"). At
    # deep deficits the safe-play call wins; the bounty info is true
    # but acting on it gets you killed and gives them MORE bounty.
    if "far_behind_safe" in kinds:
        recs = [r for r in recs if r.kind != "objective_bounty_behind"]
        kinds = {r.kind for r in recs}

    # Rule 11 — spiral-level tilt (alert) on the active player suppresses
    # offensive prompts. Telling a feeding player "fight now!" is the
    # worst possible combo; the tilt rec is asking them to do nothing.
    has_spiral_tilt = any(
        r.kind == "tilt" and r.severity == "alert" for r in recs
    )
    if has_spiral_tilt:
        _spiral_drop = {
            "fight", "numbers_adv", "gold_lead", "kill_lead",
            "power_spike",       # ult-up "play now" contradicts "do nothing"
            "lane_level_adv",    # lane-trade window irrelevant while spiraling
            "lane_mia",          # don't tell a feeding player to side-push alone
            "objective_setup",   # objective prep irrelevant when player is feeding
            "skill_point_unspent",  # micro-nag drowns out "do nothing" message
            "enemy_bounty",      # focus call irrelevant — feeding player should not fight
            "ally_bounty",       # protect-the-carry irrelevant — feeding player can't help
            "matchup_mismatch",  # lane analysis is what tilt already implies — single message wins
            "plate_window",      # don't tell a feeding player to push for plates
            "teamfight_won",     # feeding player isn't pushing the win — let team handle
            "teamfight_won_big",
            "shutdown_taken",    # feeding player shouldn't be the one pushing
            # objective_taken_* — feeding player shouldn't carry the baron-push lead
            "objective_taken_baron",
            "objective_taken_elder",
            "objective_taken_soul",
            "objective_taken_drake",
            "objective_taken_herald",
            "objective_bounty_behind",  # feeding player shouldn't force comeback objective
            "objective_bounty_ahead",
            "flash_down", "tp_down", "combat_spell_down",
            "jungler_down", "dragon_soul",
        }
        recs = [r for r in recs if r.kind not in _spiral_drop]

    return recs


def evaluate(
    snapshot: "LcdaSnapshot | None",
    *,
    rules: tuple = ALL_RULES,
    spell_tracker: "SpellTracker | None" = None,
    situational_build: object = None,
) -> list[Recommendation]:
    """Run every rule against ``snapshot`` and return the non-None
    results sorted by severity (alerts first). Pure function — safe
    to call on the LCDA-snapshot tick without any state.

    None snapshot → empty list (pre-game window). Rules that raise
    are silently skipped — a buggy rule must not break the engine.

    ``spell_tracker``: when provided, enables context-aware rules that
    require user-tracked summoner spell cooldowns (e.g. flash_down).

    ``situational_build``: a ``BuildResult`` from the build engine.
    When provided, fires ``rule_situational_build`` with item recs
    adjusted for the live enemy team composition.
    """
    if snapshot is None:
        return []
    out: list[Recommendation] = []
    for rule in rules:
        try:
            rec = rule(snapshot)
        except Exception:  # noqa: BLE001 — engine never propagates rule bugs
            continue
        if rec is not None:
            out.append(rec)
    if spell_tracker is not None:
        for _spell_rule in (
            rule_enemy_flash_down,
            rule_enemy_tp_down,
            rule_enemy_combat_spell_down,
        ):
            try:
                rec = _spell_rule(snapshot, spell_tracker)
                if rec is not None:
                    out.append(rec)
            except Exception:  # noqa: BLE001
                pass
    if situational_build is not None:
        try:
            rec = rule_situational_build(snapshot, situational_build)
            if rec is not None:
                out.append(rec)
        except Exception:  # noqa: BLE001
            pass
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    return _suppress_dominated(out)
