"""Tag every emitted recommendation with which part of the locked
WinCondition it serves.

Pro-coaching coherence: when the user sees "Mortal Reminder JETZT"
in-game, the small italic anchor under it ("→ Threat Response: gegen
Sustain") makes every call feel like part of the same plan rather
than a list of disconnected hints.

Mapping is rule-kind-driven, with a few branches that look at
``WinCondition.raw_tags`` for matchup-specific routing (build_swap
goes to threat_response when the matchup carries any threat signal,
falls back to primary_path otherwise).

Pure function: takes a Recommendation, returns a Recommendation. Never
raises — unmapped kinds get an empty tag and the UI renders no anchor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..win_condition import WinCondition
    from ._core import Recommendation


# Allowed win_path values. The UI maps these to a colored italic anchor:
#
#   primary_path     — "back to plan" / general directive (color: ACCENT)
#   spike_window     — power-spike-driven call (color: SUCCESS)
#   threat_response  — counter-play to a known enemy threat (color: DANGER)
#   avoid_mistake    — preventing the "never do" mistake (color: WARNING)
#   closing_window   — kill / ace / inhib pressure (color: ACCENT_BRIGHT)
#
# An empty string means "no anchor" and the UI shows no italic line.
WIN_PATH_PRIMARY = "primary_path"
WIN_PATH_SPIKE = "spike_window"
WIN_PATH_THREAT = "threat_response"
WIN_PATH_AVOID = "avoid_mistake"
WIN_PATH_CLOSING = "closing_window"


# Static rule-kind → win_path mapping. Most rules sort cleanly into
# one bucket. The few that need matchup context get handled in
# ``_dynamic_route``.
_STATIC_KIND_TO_PATH: dict[str, str] = {
    # Closing windows — late-game pressure / endgame.
    "ace_detected":          WIN_PATH_CLOSING,
    "game_end":              WIN_PATH_CLOSING,
    "enemy_inhibitor_down":  WIN_PATH_CLOSING,
    "enemy_inhib_expiring":  WIN_PATH_CLOSING,
    "enemy_base_exposed":    WIN_PATH_CLOSING,
    "shutdown_taken":        WIN_PATH_CLOSING,
    "kill_lead_snowball":    WIN_PATH_CLOSING,
    "first_blood":           WIN_PATH_CLOSING,
    "objective_taken_by_ally": WIN_PATH_CLOSING,
    "fight_window_closing":  WIN_PATH_CLOSING,
    "numbers_advantage":     WIN_PATH_CLOSING,

    # Power spikes — match the existing spike rules.
    "power_spike":           WIN_PATH_SPIKE,
    "enemy_item_spike":      WIN_PATH_SPIKE,

    # Threat response — defending against enemy aggression.
    "gank_risk":             WIN_PATH_THREAT,
    "lane_opponent_mia":     WIN_PATH_THREAT,
    "tilt_detection":        WIN_PATH_THREAT,
    "matchup_mismatch":      WIN_PATH_THREAT,
    "enemy_jungler_down":    WIN_PATH_THREAT,
    "enemy_dragon_soul":     WIN_PATH_THREAT,
    "enemy_herald_danger":   WIN_PATH_THREAT,
    "enemy_baron_buff":      WIN_PATH_THREAT,
    "enemy_elder_buff":      WIN_PATH_THREAT,
    "enemy_combat_spell_down": WIN_PATH_THREAT,
    "enemy_flash_down":      WIN_PATH_THREAT,
    "enemy_tp_down":         WIN_PATH_THREAT,
    "enemy_bounty":          WIN_PATH_THREAT,

    # Avoid the mistake — the rules that scream "don't fight".
    "numbers_disadvantage":  WIN_PATH_AVOID,
    "kill_deficit_defensive": WIN_PATH_AVOID,
    "far_behind_safe":       WIN_PATH_AVOID,
    "ally_inhib_down":       WIN_PATH_AVOID,
    "ally_turret_lost":      WIN_PATH_AVOID,
    "active_bounty":         WIN_PATH_AVOID,
    "ally_inhib_respawning": WIN_PATH_AVOID,

    # Primary path — general tempo / objective / build calls.
    "recall_check":          WIN_PATH_PRIMARY,
    "unspent_skill_points":  WIN_PATH_PRIMARY,
    "objective_setup_window": WIN_PATH_PRIMARY,
    "objective_bounty_active": WIN_PATH_PRIMARY,
    "ally_bounty":           WIN_PATH_PRIMARY,
    "lane_pressure":         WIN_PATH_PRIMARY,
    "level_deficit":         WIN_PATH_PRIMARY,
    "lane_level_advantage":  WIN_PATH_PRIMARY,
    "cs_deficit":            WIN_PATH_PRIMARY,
    "late_game_group":       WIN_PATH_PRIMARY,
    "dragon_soul_pressure":  WIN_PATH_PRIMARY,
    "void_grubs":            WIN_PATH_PRIMARY,
    "ally_herald_window":    WIN_PATH_PRIMARY,
    "plate_window":          WIN_PATH_PRIMARY,
    "baron_buff_expiring":   WIN_PATH_PRIMARY,
    "teamfight_outcome":     WIN_PATH_PRIMARY,
    "fight_opportunity":     WIN_PATH_PRIMARY,
    "gold_lead_push":        WIN_PATH_PRIMARY,
    "elder_buff_expiring":   WIN_PATH_PRIMARY,
    # Objective windows fold into primary_path — they're "what to do
    # next on the map", not a closing call until the corresponding
    # buff actually drops.
    "drake_priority":        WIN_PATH_PRIMARY,
    "dragon_window":         WIN_PATH_PRIMARY,
    "drake_give_up":         WIN_PATH_AVOID,
    "elder_window":          WIN_PATH_PRIMARY,
    "baron_priority":        WIN_PATH_PRIMARY,
    "baron_window":          WIN_PATH_PRIMARY,
    "baron_give_up":         WIN_PATH_AVOID,
    "herald_priority":       WIN_PATH_PRIMARY,
}


def _dynamic_route(rec: "Recommendation", win_condition: "WinCondition | None") -> str:
    """Route the rule kinds whose path depends on the matchup. Falls
    through to ``_STATIC_KIND_TO_PATH`` when no override applies."""
    kind = rec.kind
    if kind == "build_swap":
        # If the matchup carries any threat signal the swap is a
        # threat response (anti-burst, anti-CC, anti-mobility,
        # anti-sustain). Otherwise it's primary-path tempo.
        if win_condition and any(
            t in win_condition.raw_tags
            for t in ("burst_threat", "mobility_threat",
                      "cc_threat", "sustain_threat")
        ):
            return WIN_PATH_THREAT
        return WIN_PATH_PRIMARY
    if kind == "situational_build":
        # Same reasoning — situational items target enemy-team flavors.
        if win_condition and win_condition.raw_tags:
            return WIN_PATH_THREAT
        return WIN_PATH_PRIMARY
    return ""


def tag_recommendation(
    rec: "Recommendation",
    win_condition: "WinCondition | None",
) -> "Recommendation":
    """Return a copy of ``rec`` with ``win_path`` populated.

    Already-tagged recs are returned unchanged so a rule that explicitly
    sets ``win_path`` (e.g. for a fully bespoke routing) wins. Unmapped
    rule kinds get an empty tag (UI renders no anchor).
    """
    from dataclasses import replace
    if rec.win_path:
        return rec
    path = _dynamic_route(rec, win_condition)
    if not path:
        path = _STATIC_KIND_TO_PATH.get(rec.kind, "")
    if not path:
        return rec
    return replace(rec, win_path=path)


__all__ = [
    "tag_recommendation",
    "WIN_PATH_PRIMARY",
    "WIN_PATH_SPIKE",
    "WIN_PATH_THREAT",
    "WIN_PATH_AVOID",
    "WIN_PATH_CLOSING",
]
