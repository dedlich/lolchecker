"""All rule functions + the ALL_RULES tuple.

Each rule is a pure function ``(snapshot) -> Recommendation | None``.
Constants and helpers live in ``_core``; hysteresis singletons +
reset helpers live in ``_state``. Suppression logic that culls
contradictory recs lives in ``_evaluate``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from collections.abc import Callable

if TYPE_CHECKING:
    from ...lcda.source import LcdaSnapshot

# Bring everything from _core and _state into this module's namespace
# so rule bodies (copied verbatim from the pre-split file) keep working
# without surgery on every reference. The wildcard imports are safe
# here because both modules expose only their public surface.
from ._core import *  # noqa: F401,F403
from ._state import *  # noqa: F401,F403

# Hysteresis singletons are underscore-prefixed and so don't ride along
# on the wildcard. Each rule that maintains tick-to-tick state reads /
# writes its module-level singleton; explicitly re-bind those here.
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
# Helpers that live in _core but are underscore-prefixed need explicit
# imports too — the wildcard skips them.
from ._core import (
    _CHAMP_DATA,
    _COMBAT_SPELLS,
    _DRAKE_DISPLAY,
    _active_ally_inhibitors_down,
    _active_enemy_inhibitors_down,
    _active_player,
    _aoe_cc_warnings,
    _ally_baron_buff_remaining,
    _ally_elder_buff_remaining,
    _alive_count,
    _avg_level_diff,
    _drake_stack_count,
    _earliest_ally_inhib_respawn_remaining,
    _earliest_enemy_inhib_respawn_remaining,
    _enemy_baron_buff_remaining,
    _ally_grub_count,
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


# rule_drake_priority + rule_drake_give_up → rules/objectives.py


# rule_gold_lead_push + rule_far_behind_safe + rule_level_deficit → rules/meta.py


# ---------------------------------------------------------------------------
# CS deficit and lane-level-advantage rules
# ---------------------------------------------------------------------------

# Minimum game-time before CS efficiency has enough data to be meaningful.
CS_MIN_GAME_TIME_S = 240.0         # 4 min
# Suppress in late game where grouping unavoidably drops CS/min.
CS_LATE_SUPPRESS_S = 1680.0        # 28 min
# Target farm rate for lane players (emerald+ average).
CS_EXPECTED_PER_MIN = 8.0
# How far below expected before we fire.
CS_INFO_DEFICIT = 2.0              # info  (< 6.0/min when expected 8.0)
CS_WARN_DEFICIT = 3.5              # warn  (< 4.5/min when expected 8.0)
# Long TTL so the rule fires once per ~2 ticks, not every 2 s tick.
CS_DEFICIT_TTL_S = 30.0
# Positions exempt from CS checks.
_NON_CS_POSITIONS: frozenset[str] = frozenset({"UTILITY", "JUNGLE"})

# Lane-level advantage thresholds (laning phase only).
LANE_LEVEL_ADV_THRESHOLD = 2      # 2-level edge = real advantage
LANE_LEVEL_DOM_THRESHOLD = 3      # 3-level edge = dominance
LANE_PHASE_CUTOFF_S = 1200.0      # 20 min


def rule_cs_deficit(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the active laner's CS/min is significantly below the
    expected farming rate (≈8 CS/min for lane roles). Excludes supports
    and junglers who have different gold income paths. Suppressed before
    4 min (not enough waves) and after 28 min (grouping reduces farm).
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time < CS_MIN_GAME_TIME_S or game_time >= CS_LATE_SUPPRESS_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if position in _NON_CS_POSITIONS:
        return None

    cs = getattr(player, "creep_score", 0)
    if not isinstance(cs, int) or cs < 0:
        return None

    cs_per_min = cs / (game_time / 60.0)
    deficit = CS_EXPECTED_PER_MIN - cs_per_min
    if deficit < CS_INFO_DEFICIT:
        return None

    severity = "warn" if deficit >= CS_WARN_DEFICIT else "info"
    min_elapsed = int(game_time / 60)
    expected_cs = int(CS_EXPECTED_PER_MIN * min_elapsed)

    return Recommendation(
        text=f"CS {cs} ({cs_per_min:.1f}/min, Ziel {CS_EXPECTED_PER_MIN:.0f}/min) — farme Wellen",
        severity=severity,
        category="lane",
        confidence=0.75,
        risk="LOW",
        ttl_s=CS_DEFICIT_TTL_S,
        kind="cs_deficit",
        reasons=(
            f"{cs_per_min:.1f} CS/min (Ziel: {CS_EXPECTED_PER_MIN:.0f}/min)",
            f"Minute {min_elapsed}: {cs} CS, Soll ~{expected_cs}",
        ),
    )


def rule_lane_level_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a meaningful level edge in the active player's lane matchup.

    Level 2+ lead over the lane opponent is a reliable all-in / trade
    window that many players miss. Level 2+ deficit is an additional
    safety reminder on top of the team-average rule_level_deficit.
    Only fires during the laning phase (< 20 min) and only when LCDA
    exposes position data for both players so the matchup is unambiguous.
    """
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time >= LANE_PHASE_CUTOFF_S:
        return None

    player = _active_player(snapshot)
    if player is None:
        return None

    position = (getattr(player, "position", "") or "").upper()
    if not position or position in _NON_CS_POSITIONS:
        return None

    # Find the enemy at the same position — LCDA sets position for all 10.
    enemies = list(getattr(snapshot, "enemies", []) or [])
    lane_opp = next(
        (e for e in enemies if (getattr(e, "position", "") or "").upper() == position),
        None,
    )
    if lane_opp is None:
        return None

    my_level = int(getattr(player, "level", 0) or 0)
    opp_level = int(getattr(lane_opp, "level", 0) or 0)
    diff = my_level - opp_level

    if abs(diff) < LANE_LEVEL_ADV_THRESHOLD:
        return None

    opp_name = getattr(lane_opp, "champion_name", "") or "Gegner"

    if diff >= LANE_LEVEL_DOM_THRESHOLD:
        return Recommendation(
            text=f"Level-Dominanz +{diff} vs {opp_name} — All-in erzwingen",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=25.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                f"+{diff} Level = statistisch gewonnener All-in",
            ),
        )
    if diff >= LANE_LEVEL_ADV_THRESHOLD:
        return Recommendation(
            text=f"Level-Vorteil +{diff} vs {opp_name} — Trade-Fenster",
            severity="info",
            category="lane",
            confidence=0.75,
            risk="LOW",
            ttl_s=20.0,
            kind="lane_level_adv",
            reasons=(
                f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
                "Level-Edge: Trade erzwingen oder Turm-Plates nehmen",
            ),
        )
    # diff <= -LANE_LEVEL_ADV_THRESHOLD → enemy level lead
    return Recommendation(
        text=f"Level-Nachteil {diff} vs {opp_name} — Safe farmen",
        severity="warn",
        category="lane",
        confidence=0.80,
        risk="HIGH",
        ttl_s=20.0,
        kind="lane_level_disadv",
        reasons=(
            f"Du: Level {my_level}, {opp_name}: Level {opp_level}",
            f"{diff} Level = {opp_name} gewinnt jeden Trade",
        ),
    )


# rule_baron_priority + rule_baron_give_up + rule_herald_priority → rules/objectives.py


# rule_kill_lead_snowball + rule_kill_deficit_defensive +
# rule_numbers_disadvantage + rule_numbers_advantage → rules/combat.py


# rule_late_game_group → rules/meta.py


# --------------------------------------------------------------------------
# Window rules — pro-level objective + fight decision trees
# --------------------------------------------------------------------------

# rule_dragon_window → rules/objectives.py


# rule_elder_window + rule_baron_window → rules/objectives.py


# rule_fight_opportunity → rules/combat.py


# rule_enemy_base_exposed → rules/inhibitors.py (re-imported below)


def rule_lane_pressure(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy outer or inner turrets down → push that lane for objectives.

    Fully open lane (both outer + inner fallen) signals an inhib threat
    and forces enemy rotations — use it to enable drake/baron vision.
    Partial open (only outer) is an info nudge to send waves.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    turrets_down = _enemy_turrets_down(snapshot)
    if not turrets_down:
        return None
    fully_open = [lane for lane, n in turrets_down.items() if n >= 2]
    partially_open = [lane for lane, n in turrets_down.items() if n == 1]
    if fully_open:
        lanes_str = " + ".join(sorted(fully_open))
        return Recommendation(
            text=f"{lanes_str}-Lane offen bis Inhib — Pressure + Obj-Vision!",
            severity="warn",
            category="lane",
            confidence=0.82,
            risk="LOW",
            ttl_s=60.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer + Inner Tower fallen",
                "Inhib angreifbar — zwingt Rotationen",
                "Super-Minions nach Inhib = passiver Pressure",
            ),
        )
    if partially_open:
        lanes_str = " + ".join(sorted(partially_open))
        return Recommendation(
            text=f"{lanes_str}-Lane Outer down — Side-Waves pushen",
            severity="info",
            category="lane",
            confidence=0.74,
            risk="LOW",
            ttl_s=45.0,
            kind="lane_open",
            reasons=(
                f"Enemy {lanes_str}: Outer Tower fallen",
                "Side-Waves erzeugen Rotationsdruck",
            ),
        )
    return None


# rule_ace_detected → rules/combat.py


# rule_enemy_herald_danger + rule_ally_herald_window → rules/objectives.py


def rule_ally_turret_lost(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one of OUR turrets within the last 60 seconds.

    Fires a short-lived defensive nudge: recall to clear the wave, prevent
    the enemy from extending the advantage. Severity scales with turret tier:
      P1 (Outer)  → info   — wave-clear + rotate
      P2 (Inner)  → warn   — base is now reachable
      P3 (Inhib)  → alert  — inhibitor turret gone, base siege imminent

    Only fires within ALLY_TURRET_ALERT_WINDOW_S (60s) of the kill so the
    signal doesn't linger for the rest of the game. Not suppressed by
    numbers_disadv (defensive signals remain relevant while short-handed).
    """
    losses = _recent_ally_turret_losses(snapshot)
    if not losses:
        return None

    # Escalate to the highest-tier recent loss.
    tier_rank = {"P3": 3, "P2": 2, "P1": 1}
    losses.sort(key=lambda t: tier_rank.get(t[1], 0), reverse=True)
    lane, tier, _side, evt_time = losses[0]
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    age_s = int(game_time - evt_time)
    gold = _team_gold_diff(snapshot)

    if tier == "P3":
        return Recommendation(
            text=f"Inhib-Turm {lane} verloren — SOFORT Basis verteidigen!",
            severity="alert",
            category="safety",
            confidence=0.88,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inhib-Turm (vor {age_s}s)",
                "Inhib jetzt angreifbar — Super-Minions drohen!",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    if tier == "P2":
        return Recommendation(
            text=f"Inner {lane}-Turm verloren — Wave claren, Basis absichern",
            severity="warn",
            category="safety",
            confidence=0.82,
            risk="HIGH",
            ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
            kind="ally_turret_lost",
            reasons=(
                f"Gegner zerstörte unseren {lane} Inner Tower (vor {age_s}s)",
                "Lane offen bis Inhib-Turm — Wellen drohen Base",
                f"Gold-Diff: {gold:+d}",
            ),
        )
    # P1 — outer turret
    return Recommendation(
        text=f"Outer {lane}-Turm verloren — Welle claren, dann reagieren",
        severity="info",
        category="safety",
        confidence=0.74,
        risk="MEDIUM",
        ttl_s=max(0.0, ALLY_TURRET_ALERT_WINDOW_S - age_s),
        kind="ally_turret_lost",
        reasons=(
            f"Gegner zerstörte unseren {lane} Outer Tower (vor {age_s}s)",
            "Lane jetzt nur noch Inner Tower — reagieren!",
            f"Gold-Diff: {gold:+d}",
        ),
    )


# rule_dragon_soul_pressure + rule_void_grubs + rule_enemy_jungler_down +
# rule_enemy_dragon_soul → rules/objectives.py


# rule_ally_inhib_respawning, rule_ally_inhib_down, rule_enemy_inhibitor_down,
# rule_enemy_inhib_expiring → rules/inhibitors.py (re-imported below)


def rule_game_ended(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface a final Win/Loss card when the GameEnd event is present.

    Shows ally drake count and final gold diff as context. Once this fires,
    _suppress_dominated drops all other recommendations — the game is over.
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


# rule_baron_buff_expiring + rule_enemy_baron_buff + rule_enemy_elder_buff +
# rule_elder_buff_expiring → rules/objectives.py


# Domain rule modules — re-exported so legacy imports
# (``from ._rules import rule_xyz`` / ``from ._rules import BOUNTY_TIER_*``)
# keep working while §3.2 of OPTIMIZATION.md is in flight. Each domain
# file under ``rules/`` owns its rules + helper constants.
from .rules.bounty import (  # noqa: E402,F401
    OBJECTIVE_BOUNTY_DIFF_THRESHOLD,
    OBJECTIVE_BOUNTY_PHASE_END_S,
    OBJECTIVE_BOUNTY_PHASE_START_S,
    OBJECTIVE_BOUNTY_REARM_THRESHOLD,
    rule_active_bounty,
    rule_ally_bounty,
    rule_enemy_bounty,
    rule_objective_bounty_active,
)
from .rules.combat import (  # noqa: E402,F401
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
from .rules.inhibitors import (  # noqa: E402
    rule_ally_inhib_down,
    rule_ally_inhib_respawning,
    rule_enemy_base_exposed,
    rule_enemy_inhib_expiring,
    rule_enemy_inhibitor_down,
)
from .rules.meta import (  # noqa: E402
    rule_far_behind_safe,
    rule_gold_lead_push,
    rule_late_game_group,
    rule_level_deficit,
)
from .rules.personal import (  # noqa: E402,F401
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
from .rules.objectives import (  # noqa: E402,F401
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
# Summoner-cooldown rules — NOT in ``ALL_RULES``; ``_evaluate.evaluate``
# calls them in a separate loop when ``spell_tracker is not None``.
from .rules.summoner_cd import (  # noqa: E402
    rule_enemy_combat_spell_down,
    rule_enemy_flash_down,
    rule_enemy_tp_down,
)


# rule_fight_window_closing → rules/combat.py


# rule_power_spike + rule_enemy_item_spike → rules/personal.py
# _TILT_LANE_PHASE_END_S, _TILT_MID_GAME_END_S, _tilt_phase_advice → rules/personal.py


# Recall-window thresholds + rule_recall_check → rules/personal.py



# rule_recall_check + SKILL_POINT_* + rule_unspent_skill_points → rules/personal.py


# BOUNTY_TIER_*, _ALLY_PROTECT_ADVICE, rule_active_bounty / rule_enemy_bounty /
# rule_ally_bounty → rules/bounty.py (re-imported below)


# rule_active_bounty body → rules/bounty.py


# rule_enemy_bounty + rule_ally_bounty bodies (and _ALLY_PROTECT_ADVICE) → rules/bounty.py


# ─── Matchup-mismatch thresholds ─────────────────────────────────────────────
# Pros distinguish "I'm tilting" from "I'm losing this lane specifically".
# A 0-3 score with all 3 deaths to the same enemy is a hardstomp — the
# coaching is different from a 0-3 spread across team fights.

MISMATCH_DEFICIT_INFO: int = 2   # net 2 = you're behind in the matchup
MISMATCH_DEFICIT_WARN: int = 3   # net 3+ = lane is lost, defensive only


def _matchup_deficit(active_ids: set[str], events: list[dict]) -> dict[str, int]:
    """For each enemy who has interacted with the active player in
    ``ChampionKill`` events, return ``deaths_from_them − kills_on_them``.

    Positive deficits = you are losing the matchup against that enemy.
    Negative or zero = you're even or winning. Only enemies appearing in
    at least one event are returned.
    """
    deficits: dict[str, int] = {}
    for evt in events:
        if evt.get("EventName") != "ChampionKill":
            continue
        killer = evt.get("KillerName") or ""
        victim = evt.get("VictimName") or ""
        if victim in active_ids and killer:
            deficits[killer] = deficits.get(killer, 0) + 1
        elif killer in active_ids and victim:
            deficits[victim] = deficits.get(victim, 0) - 1
    return deficits


def rule_matchup_mismatch(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "you're losing the lane to a specific enemy" once per
    deficit-tier per matchup (Charter B5 — matchup awareness).

    Difference vs ``rule_tilt_detection``:
      * Tilt:    aggregate death cadence — "you keep dying"
      * Matchup: per-killer deficit — "you keep dying *to this enemy*"

    A 0-3 score with all three deaths to one enemy = hardstomp matchup.
    A 0-3 spread across team fights = just tilt, this rule stays silent.
    Both can fire together when the player both is on a death streak AND
    the streak comes mostly from one opponent — the messages are
    complementary (tilt = "stop fighting"; mismatch = "you specifically
    can't 1v1 *this* enemy, freeze the wave and wait for help").

    Tier ladder (deficit = deaths_from_X − kills_on_X):
      * deficit 2  → info — "X tötet dich oft, defensiv farmen"
      * deficit 3+ → warn — "X dominiert dich, Lane verloren, Hilfe nötig"

    Per-enemy hysteresis fires once per tier per game; subsequent
    deaths to the same enemy at the same tier don't re-spam. The
    deficit can shrink (you kill them) which doesn't auto-rearm —
    once flagged, the matchup info stays useful.
    """
    active = _active_player(snapshot)
    if active is None:
        return None
    sn = str(getattr(active, "summoner_name", "") or "")
    cn = str(getattr(active, "champion_name", "") or "")
    active_ids: set[str] = {x for x in (sn, cn) if x}
    if not active_ids:
        return None

    events = list(getattr(snapshot, "raw_events", []) or [])
    deficits = _matchup_deficit(active_ids, events)
    if not deficits:
        return None

    # Pick the worst (highest) deficit. If multiple are tied, alphabetical
    # for determinism.
    h = _MATCHUP_MISMATCH_HYSTERESIS
    best: tuple[int, int, str] | None = None  # (tier, deficit, name)
    for name, deficit in deficits.items():
        if deficit < MISMATCH_DEFICIT_INFO:
            continue
        if deficit >= MISMATCH_DEFICIT_WARN:
            tier = MISMATCH_DEFICIT_WARN
        else:
            tier = MISMATCH_DEFICIT_INFO
        if tier <= h.last_fired_tier.get(name, 0):
            continue
        candidate = (tier, deficit, name)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, deficit, name = best
    h.last_fired_tier[name] = tier

    if tier >= MISMATCH_DEFICIT_WARN:
        text = (
            f"{name} dominiert dich ({deficit} Diff) — "
            f"Lane verloren. Welle freezen, Hilfe pingen, kein Trade."
        )
        severity, ttl_s, confidence, risk = "warn", 35.0, 0.90, "HIGH"
    else:
        text = (
            f"{name} tötet dich oft ({deficit} Diff) — "
            f"Matchup-Vorsicht: defensiv farmen, Jungler-Hilfe einplanen."
        )
        severity, ttl_s, confidence, risk = "info", 30.0, 0.80, "MEDIUM"

    return Recommendation(
        text=text,
        severity=severity,
        category="lane",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="matchup_mismatch",
        reasons=(
            f"Tode gegen {name}: {deficit} mehr als Kills auf {name}",
            "Matchup-Mismatch isolieren von Tilt: das ist diese Lane, nicht das Spiel",
            "Welle freezen + Jungle-Hilfe = einziger gesunder Komeback-Pfad",
        ),
    )


# ─── Plate-window thresholds ────────────────────────────────────────────────
# Outer turret plates exist 0:00 – 14:00. Each pops for 160g + a chunk of the
# turret's HP. After 14:00 plates despawn — uncashed plates are pure waste.
# Pros aggressively trade waves to take plates because:
#   * 5 plates × 6 outer turrets = 30 plates total, ~4800g of free gold
#   * Plates damage the turret too, accelerating tower kills mid-game
#   * Once plates fall off the turret has no resistance bonus → faster siege
PLATE_WINDOW_OPEN_S: float = 780.0    # 13:00 — final-call reminder kicks in
PLATE_WINDOW_CLOSE_S: float = 840.0   # 14:00 — plates despawn (Riot fixed)


def rule_plate_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fire once at ~13:00 game time to remind about despawning plates.

    The most expensive lesson early-mid game players learn: plates fall
    off at 14:00 and any plate you didn't pop is gone forever. At 13:00
    you have ~60 s to crash a wave + take whatever plates you can reach.

    Single-fire (hysteresis) so the reminder doesn't spam. Doesn't fire
    before 13:00 (less urgent — you have time) or after 14:00 (too late).
    """
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < PLATE_WINDOW_OPEN_S or game_time >= PLATE_WINDOW_CLOSE_S:
        return None
    h = _PLATE_WINDOW_HYSTERESIS
    if h.fired:
        return None
    h.fired = True

    remaining = int(PLATE_WINDOW_CLOSE_S - game_time)
    return Recommendation(
        text=(
            f"Turret-Plates fallen in {remaining}s — letzte Chance, "
            "Welle pushen, freie Plates ziehen (160g pro Plate)"
        ),
        severity="info",
        category="objective",
        confidence=0.85,
        risk="LOW",
        ttl_s=30.0,
        kind="plate_window",
        reasons=(
            f"Plates despawn bei 14:00 ({remaining}s)",
            "160g pro Plate × bis zu 30 Plates = ~4800g Tempo-Gold",
            "Nach 14:00: keine Plate-Boni mehr, naked Turrets — Siege-Phase",
        ),
    )


# rule_first_blood + TEAMFIGHT_* + _teamfight_outcome_advice → rules/combat.py


# rule_teamfight_outcome + _streak_to_tier + _shutdown_phase_advice → rules/combat.py


# rule_shutdown_taken → rules/combat.py


# OBJECTIVE_TAKEN_RECENT_S + _OBJECTIVE_LABEL + _objective_taken_advice +
# rule_objective_taken_by_ally → rules/objectives.py


# (function bodies removed — see comment above)


# OBJECTIVE_BOUNTY_* constants + rule_objective_bounty_active body → rules/bounty.py


# rule_tilt_detection → rules/personal.py


# ─── Lane-opponent MIA advice text ────────────────────────────────────────────
# Phase-aware per-lane action lines. "Push the wave" means a different play
# at 5 min (warding bushes + scouting drake) than at 14 min (Herald setup +
# tower plates). These strings are short and concrete on purpose — pros
# don't think in paragraphs.

_LANE_ADVICE_EARLY: dict[str, str] = {
    "TOP":     "Welle pushen, Top-Buschwerk wardēn",
    "MIDDLE":  "Welle pushen, Mid-River wardēn",
    "BOTTOM":  "Welle pushen, Drachen-Ward setzen",
}
_LANE_ADVICE_MID: dict[str, str] = {
    "TOP":     "Welle pushen, Plates + Herald-Spawn vorbereiten",
    "MIDDLE":  "Welle pushen, andere Lanes pingen, Mid-Roam vorbereiten",
    "BOTTOM":  "Welle pushen, Drache/Plates kontestieren",
}
LANE_PHASE_EARLY_END_S: float = 480.0   # 8:00 — early lane → mid lane


def _lane_mia_advice(active_position: str, game_time: float) -> str:
    table = _LANE_ADVICE_EARLY if game_time <= LANE_PHASE_EARLY_END_S else _LANE_ADVICE_MID
    return table.get(active_position, "Welle pushen + Vision setzen")


# SETUP_WINDOW_* + _OBJECTIVE_PRIORITY + _OBJECTIVE_SIDE + _objective_setup_advice +
# rule_objective_setup_window → rules/objectives.py


def rule_lane_opponent_mia(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "your direct lane opponent is missing" with phase-aware advice
    (Charter B2 — lane-side companion to ``rule_gank_risk``).

    Two tiers:
    * info — 30 s no CS while alive: heads-up, push the wave, scout
    * warn — 60 s no CS: they're committed elsewhere (gank, drake setup,
             roam, tower swap) — push hard + ping other lanes

    Skipped while opponent is dead (we already know exactly where they
    are: at base on a respawn timer). Skipped for JUNGLE / UTILITY
    active players — those positions don't have a single CS-tracked
    opponent.
    """
    alert = getattr(snapshot, "lane_opponent_alert", None)
    if alert is None:
        return None
    name = getattr(alert, "opponent_name", "Gegner")
    mia = int(getattr(alert, "seconds_mia", 0))
    severity = str(getattr(alert, "severity", "info"))
    pos = str(getattr(alert, "active_position", ""))
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    advice = _lane_mia_advice(pos, game_time)

    if severity == "warn":
        text = f"{name} {mia}s weg — gankt anderswo. {advice}"
        risk, ttl_s, confidence = "MEDIUM", 25.0, 0.78
    else:
        text = f"{name} weg ({mia}s) — {advice}"
        risk, ttl_s, confidence = "LOW", 18.0, 0.70

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="lane_mia",
        reasons=(
            f"{name} hat seit {mia}s kein CS gemacht",
            f"Position: {pos}",
            "Welle pushen = ihr CS leakt + du tempogewinnst",
        ),
    )


def rule_gank_risk(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when the enemy jungler has been unaccounted-for long enough
    to be approaching a lane undetected (Charter B2).

    Uses the GankAlert computed by LcdaSource from ChampionKill event
    timestamps. 60s MIA → info; 90s MIA → warn. Only fires during
    laning phase (4–20 min) for lane roles (TOP, MID, BOT).
    """
    alert = getattr(snapshot, "gank_alert", None)
    if alert is None:
        return None
    jungler = getattr(alert, "jungler_name", "Jungler")
    mia = int(getattr(alert, "seconds_mia", 0))
    severity = str(getattr(alert, "severity", "info"))
    if severity == "warn":
        text = f"{jungler} seit {mia}s verschwunden — Gank möglich! Welle räumen oder zurückziehen."
        risk = "HIGH"
        ttl_s = 15.0
    else:
        text = f"{jungler} seit {mia}s nicht gesehen — Vorsicht in der Lane."
        risk = "MEDIUM"
        ttl_s = 12.0
    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.72,
        risk=risk,
        ttl_s=ttl_s,
        kind="gank_risk",
        reasons=(f"Jungler {jungler} nicht in Kill-Events seit {mia}s",),
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


# ─── Situational Build Rule ───────────────────────────────────────────────────

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

    # Pick the top 3 situational items and build a concise recommendation.
    top = situational[:3]

    # Collect context-driven reasons (lines that mention enemy comp adjustments).
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


