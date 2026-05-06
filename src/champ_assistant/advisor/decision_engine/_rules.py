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
    _team_kill_diff,
)


def rule_drake_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Drake spawning soon AND we have resources → contest it.

    "Resources" today: not significantly behind in gold OR ahead in
    levels. The full version would also check ult availability +
    summoner CDs; we don't have that signal.
    """
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD and levels < 0:
        # Resource-poor: don't force. The drake_give_up rule handles this.
        return None
    return Recommendation(
        text=f"Drache spawnt in {int(remaining)}s — Vision setzen, Side gruppieren",
        severity="alert",
        category="objective",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=remaining,
        reasons=(
            f"Drache spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d}",
            f"Level-Diff: {levels:+.1f}",
        ),
    )


def rule_drake_give_up(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Drake up but we're significantly behind → don't contest, take
    side waves instead. Better to give up the objective than feed."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None  # not behind enough to skip
    return Recommendation(
        text=f"Drache ({int(remaining)}s) abgeben — Side-Wellen pushen, "
             f"Gold-Diff aufholen",
        severity="warn",
        category="objective",
        confidence=0.80,
        risk="HIGH",
        ttl_s=remaining,
        reasons=(
            f"Drache spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d} (unter -{GOLD_DEFICIT_THRESHOLD})",
            "Contest = Risk vs Reward negativ",
        ),
    )


def rule_gold_lead_push(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Significant team gold lead → convert into map pressure (vision,
    plates, objectives) instead of just farming."""
    gold = _team_gold_diff(snapshot)
    if gold < GOLD_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"+{gold} Gold — Vision + Objective pushen",
        severity="info",
        category="tempo",
        confidence=0.75,
        risk="LOW",
        ttl_s=20.0,
        kind="gold_lead",
        reasons=(
            f"Team-Gold-Vorsprung: +{gold}",
            "Über Schwelle für aktiven Tempo-Push",
            "Nächstes Objective sollte priorisiert werden",
        ),
    )


def rule_far_behind_safe(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Significant deficit → safe play, wave clear, don't force fights."""
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"{gold} Gold — Safe spielen, Wellen abräumen, keine Fights",
        severity="warn",
        category="safety",
        confidence=0.80,
        risk="HIGH",
        ttl_s=30.0,
        kind="far_behind_safe",
        reasons=(
            f"Team-Gold-Diff: {gold} (unter -{GOLD_DEFICIT_THRESHOLD})",
            "Fights statistisch verloren",
            "Wave-Clear sichert XP + Gold ohne Risiko",
        ),
    )


def rule_level_deficit(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Average level gap large enough that any fair fight loses."""
    diff = _avg_level_diff(snapshot)
    if diff > -LEVEL_GAP_THRESHOLD:
        return None
    return Recommendation(
        text=f"Level-Nachteil ({diff:+.1f}) — XP-Wellen sichern, "
             f"keine Skirmishes",
        severity="warn",
        category="safety",
        confidence=0.78,
        risk="HIGH",
        ttl_s=20.0,
        reasons=(
            f"Avg-Level-Diff: {diff:+.1f}",
            f"Schwelle: ±{LEVEL_GAP_THRESHOLD}",
            "Fair fights gehen verloren bei Level-Disparität",
        ),
    )


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


def _active_player(snapshot: "LcdaSnapshot") -> object | None:
    """Return the active player's LivePlayer record from the allies list."""
    name = getattr(snapshot, "active_summoner", "") or ""
    if not name:
        return None
    for p in (getattr(snapshot, "allies", []) or []):
        if getattr(p, "summoner_name", "") == name:
            return p
    return None


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


def rule_baron_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Baron up soon AND we have resources → set up vision + group.
    Baron's window is wider than drake (45s vs 30s) because the prep
    matters more — wave clear, vision sweep, ult availability check."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD:
        return None  # baron_give_up handles the behind case
    return Recommendation(
        text=f"Baron in {int(remaining)}s — Vision-Pinks setzen, "
             f"Side-Wellen prep, Ults checken",
        severity="alert",
        category="objective",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d}",
            "Baron-Buff = Game-Winner — Setup-Phase kritisch",
        ),
    )


def rule_baron_give_up(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Baron up but we're significantly behind → don't contest. A
    Baron-throw at 8k behind is a 14-day vacation."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold > -GOLD_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"Baron ({int(remaining)}s) abgeben — defensiv warten, "
             f"Konter-Engage suchen",
        severity="warn",
        category="objective",
        confidence=0.82,
        risk="HIGH",
        ttl_s=remaining,
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d} (deutlich hinten)",
            "Baron-Throw = 14-Tage Vacation",
        ),
    )


def rule_herald_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Herald is an early-game tower-plate engine. Rule only fires
    in the herald window (≤14:00) and when we're roughly even or
    ahead. No herald → silent."""
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time > HERALD_LATE_GAME_S:
        return None
    remaining = _objective_remaining(snapshot, "Herald")
    if remaining is None or remaining > DRAKE_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"Herald in {int(remaining)}s — top-side prio, "
             f"Plates abholen",
        severity="alert",
        category="objective",
        confidence=0.82,
        risk="LOW",
        ttl_s=remaining,
        reasons=(
            f"Herald spawnt in {int(remaining)}s",
            f"Game-Time: {int(game_time)}s (im Herald-Window)",
            "Herald → Plates = +400g pro Plate",
        ),
    )


def rule_kill_lead_snowball(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Substantial kill lead → press it. More aggressive vision +
    dive setups. The kill-diff signal is independent from items_value
    — you can be ahead in kills but behind in items if assists
    dominated, but the momentum is still real."""
    diff = _team_kill_diff(snapshot)
    if diff < KILL_LEAD_THRESHOLD:
        return None
    return Recommendation(
        text=f"+{diff} Kills — aggressiv Vision pushen",
        severity="info",
        category="tempo",
        confidence=0.78,
        risk="LOW",
        ttl_s=25.0,
        kind="kill_lead",
        reasons=(
            f"Team-Kill-Diff: +{diff}",
            "Momentum-Signal — Vision sollte aggressiv vorgeschoben werden",
            "Dive-Setups statt Lane-Farming",
        ),
    )


def rule_kill_deficit_defensive(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Substantial kill deficit → bunker. Don't extend, hold turret
    line, wait for a back-coordinated reset."""
    diff = _team_kill_diff(snapshot)
    if diff > -KILL_DEFICIT_THRESHOLD:
        return None
    return Recommendation(
        text=f"{diff} Kills — Bunker am Inhib, kein Überfarmen, "
             f"auf koordinierten Reset warten",
        severity="warn",
        category="safety",
        confidence=0.80,
        risk="HIGH",
        ttl_s=30.0,
        reasons=(
            f"Team-Kill-Diff: {diff}",
            "Skirmishes verlieren wir statistisch",
            "Defensive Position + koordinierter Back = nur Weg raus",
        ),
    )


def rule_numbers_disadvantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Allies dead while enemies are up → don't fight, don't extend.
    Highest-priority safety call — overrides drake/baron context.
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None  # team identity not established yet — can't compare
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    if allies_alive >= enemies_alive:
        return None
    deficit = enemies_alive - allies_alive
    if deficit <= 0:
        return None
    return Recommendation(
        text=f"Wir {allies_alive}v{enemies_alive} — KEINE Fights bis Respawn",
        severity="alert",
        category="safety",
        confidence=0.92,
        risk="HIGH",
        ttl_s=8.0,
        kind="numbers_disadv",
        reasons=(
            f"Allies alive: {allies_alive}/5",
            f"Enemies alive: {enemies_alive}/5",
            "Numbers-Disadvantage — jeder Fight = sicherer Tod",
        ),
    )


def rule_numbers_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemies dead → push the temporary 5v4 / 5v3. The window is
    short (single death = ~30s), so the rec ttl matches that."""
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    if enemies_alive >= allies_alive:
        return None
    advantage = allies_alive - enemies_alive
    if advantage <= 0:
        return None
    return Recommendation(
        text=f"{allies_alive}v{enemies_alive} — JETZT Pressure, Obj forcen!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="LOW",
        ttl_s=12.0,
        kind="numbers_adv",
        reasons=(
            f"Allies alive: {allies_alive}/5",
            f"Enemies alive: {enemies_alive}/5",
            "Window ist kurz — sofort ausnutzen",
        ),
    )


def rule_late_game_group(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Past 30:00 every teamfight decides the game. Splitpush is
    rarely worth the death timer; group as 5 around objectives."""
    game_time = getattr(snapshot, "game_time", 0.0)
    if game_time < LATE_GAME_S:
        return None
    return Recommendation(
        text="Late game — group 5, kein Splitpush ohne TP, "
             "jeder Death = 50s+",
        severity="info",
        category="tempo",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=60.0,
        reasons=(
            f"Game-Time: {int(game_time / 60)}min",
            "Death-Timer 50s+ — jeder Tod = verlorenes Objective",
            "Splitpush-Risk > Reward ohne TP-Insurance",
        ),
    )


# --------------------------------------------------------------------------
# Window rules — pro-level objective + fight decision trees
# --------------------------------------------------------------------------

def rule_dragon_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level Dragon call. Factors: timer, stack count + soul-point,
    drake type, dead-enemy free-window, gold/numbers. Replaces the
    simpler rule_drake_priority + rule_drake_give_up in ALL_RULES.
    Elder Dragon is handled by rule_elder_window — deferred here."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_SETUP_WINDOW_S:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None  # team identity not established; can't compute window quality
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    ally_stacks = _drake_stack_count(snapshot)
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    soul_point = ally_stacks >= 3          # taking this = OUR soul
    enemy_soul_point = enemy_stacks >= 3   # must deny their soul

    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    # Elder gets its own dedicated rule with higher-urgency messaging.
    if getattr(drake_obj, "detail", None) == "Elder":
        return None

    drake_name = _DRAKE_DISPLAY.get(
        getattr(drake_obj, "detail", None) or "", "Drache"
    )

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Hard give-up: significantly behind + no edge
    if gold < -GOLD_DEFICIT_THRESHOLD and numbers_diff <= 0 and not soul_point and not free_window:
        return Recommendation(
            text=f"Drache ({int(remaining)}s) abgeben — Side pushen",
            severity="warn",
            category="objective",
            confidence=0.83,
            risk="HIGH",
            ttl_s=remaining,
            kind="dragon_give",
            reasons=(
                f"Drache in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} (unter -{GOLD_DEFICIT_THRESHOLD})",
                f"Numbers: {allies_alive}v{enemies_alive}",
                "Contest = negatives Expected Value",
            ),
        )

    # Free-take window: enemies dead, we have numbers advantage
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        return Recommendation(
            text=f"Drache JETZT {allies_alive}v{enemies_alive} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=0.95,
            risk="LOW",
            ttl_s=remaining,
            kind="dragon_free",
            reasons=(
                f"FREE TAKE — {dead_names} tot ({numbers_diff} man up)",
                f"{drake_name} spawnt in {int(remaining)}s",
                f"Stacks: Wir {ally_stacks} — Gegner {enemy_stacks}",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Soul-point urgency
    if soul_point:
        return Recommendation(
            text=f"{drake_name} in {int(remaining)}s — SOUL POINT! JETZT gehen",
            severity="alert",
            category="objective",
            confidence=0.92,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="dragon_take",
            reasons=(
                f"SOUL POINT — Wir bei {ally_stacks}/4 Stacks!",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )
    if enemy_soul_point:
        return Recommendation(
            text=f"Drache in {int(remaining)}s — Gegner-Soul STOPPEN!",
            severity="alert",
            category="objective",
            confidence=0.90,
            risk="HIGH",
            ttl_s=remaining,
            kind="dragon_take",
            reasons=(
                f"GEGNER Soul Point ({enemy_stacks}/4 Stacks) — VERHINDERN!",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Active fight window (≤30s) or setup phase
    active = remaining <= DRAKE_PRIORITY_WINDOW_S and gold >= -GOLD_LEAD_THRESHOLD
    severity = "alert" if active else "warn"
    confidence = 0.84 if active else 0.78
    stack_suffix = f" ({ally_stacks}/4)" if ally_stacks > 0 else ""
    action = "JETZT Vision + Group" if active else "Vision + Group starten"

    return Recommendation(
        text=f"{drake_name}{stack_suffix} in {int(remaining)}s — {action}",
        severity=severity,
        category="objective",
        confidence=confidence,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="dragon_take",
        reasons=(
            f"{drake_name} spawnt in {int(remaining)}s",
            *(
                (f"Stacks: Wir {ally_stacks} — Gegner {enemy_stacks}",)
                if ally_stacks > 0 or enemy_stacks > 0 else ()
            ),
            f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
        ),
    )


def rule_elder_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Elder Dragon — highest-stakes drake. Fires only when Elder is the
    active spawn. Combined with Dragon Soul it is essentially a GG button;
    must always be contested or seized.

    Fires with a wider setup window (120s, same as Baron) because Elder
    vision / wave-clear preparation takes longer than regular drakes."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > BARON_SETUP_WINDOW_S:
        return None

    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    if getattr(drake_obj, "detail", None) != "Elder":
        return None  # not Elder — handled by rule_dragon_window

    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    ally_stacks = _drake_stack_count(snapshot)
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    ally_has_soul = ally_stacks >= 4
    enemy_has_soul = enemy_stacks >= 4

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Free-take: numbers advantage + dead enemies
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        soul_suffix = " + Soul = GG!" if ally_has_soul else ""
        return Recommendation(
            text=f"Elder JETZT nehmen{soul_suffix} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=0.97,
            risk="LOW",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                f"FREE ELDER — {dead_names} tot ({numbers_diff} man up)",
                f"Elder in {int(remaining)}s",
                *(("Wir haben Dragon Soul — Elder + Soul = GG!",) if ally_has_soul else ()),
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Ally has Dragon Soul → Elder closes the game
    if ally_has_soul:
        severity = "alert" if remaining <= BARON_PRIORITY_WINDOW_S else "warn"
        return Recommendation(
            text=f"Elder in {int(remaining)}s — Soul + Elder = GG JETZT!",
            severity=severity,
            category="objective",
            confidence=0.94,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                "Wir haben Dragon Soul — Elder-Buff = Execute-Schaden!",
                f"Elder in {int(remaining)}s — Soul + Elder ist unschlagbar",
                f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
            ),
        )

    # Enemy has Dragon Soul → must contest Elder at all costs
    if enemy_has_soul:
        return Recommendation(
            text=f"Elder VERHINDERN in {int(remaining)}s — Gegner-Soul + Elder = GG!",
            severity="alert",
            category="objective",
            confidence=0.92,
            risk="HIGH",
            ttl_s=remaining,
            kind="elder_take",
            reasons=(
                f"Gegner hat Dragon Soul ({enemy_stacks} Stacks)!",
                "Elder geben = Gegner unschlagbar — Contest ist Pflicht!",
                f"Elder in {int(remaining)}s | Gold-Diff: {gold:+d}",
                f"Numbers: {allies_alive}v{enemies_alive}",
            ),
        )

    # Neither team has soul (early Elder, uncommon)
    active = remaining <= BARON_PRIORITY_WINDOW_S
    severity = "alert" if active else "warn"
    return Recommendation(
        text=f"Elder-Drache in {int(remaining)}s — Gruppenbildung!",
        severity=severity,
        category="objective",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="elder_take",
        reasons=(
            f"Elder Drake in {int(remaining)}s — Execute-Buff für ganzes Team",
            f"Gold-Diff: {gold:+d} | {allies_alive}v{enemies_alive} alive",
        ),
    )


def rule_baron_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level Baron call. 120s setup window (vision + waves), 45s
    fight window. Higher stakes than Drake — one throw = potential GG.
    Replaces rule_baron_priority + rule_baron_give_up in ALL_RULES."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_SETUP_WINDOW_S:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    is_late = game_time >= LATE_GAME_S

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

    # Hard no-go: behind + no edge
    if gold < -GOLD_DEFICIT_THRESHOLD and numbers_diff <= 0 and not free_window:
        return Recommendation(
            text=f"Baron ({int(remaining)}s) abgeben — Konter suchen",
            severity="warn",
            category="objective",
            confidence=0.85,
            risk="HIGH",
            ttl_s=remaining,
            kind="baron_give",
            reasons=(
                f"Baron in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} (deutlich hinten)",
                f"Numbers: {allies_alive}v{enemies_alive}",
                "Baron-Throw = sofortiges GG",
            ),
        )

    # Free-take window: enemies dead, numbers advantage
    if free_window:
        dead_names = " + ".join(
            getattr(e, "champion_name", "?") for e in dead_enemies[:2]
        )
        confidence = min(0.97, 0.96 + (0.02 if is_late else 0.0))
        return Recommendation(
            text=f"Baron JETZT {allies_alive}v{enemies_alive} — {dead_names} tot!",
            severity="alert",
            category="objective",
            confidence=confidence,
            risk="LOW",
            ttl_s=remaining,
            kind="baron_free",
            reasons=(
                f"FREE BARON — {dead_names} tot ({numbers_diff} man up)",
                f"Baron spawnt in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
                *(("Late Game — Baron = potenzieller Game-Winner",) if is_late else ()),
            ),
        )

    # Active fight window (≤45s)
    if remaining <= BARON_PRIORITY_WINDOW_S:
        confidence = min(0.92, 0.88 + (0.03 if is_late else 0.0))
        context = f"+{gold}" if gold >= GOLD_LEAD_THRESHOLD else f"{allies_alive}v{enemies_alive}"
        return Recommendation(
            text=f"Baron in {int(remaining)}s ({context}) — Pit-Control forcen",
            severity="alert",
            category="objective",
            confidence=confidence,
            risk="MEDIUM",
            ttl_s=remaining,
            kind="baron_take",
            reasons=(
                f"Baron spawnt in {int(remaining)}s",
                f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
                f"Numbers: {allies_alive}v{enemies_alive} alive",
                *(("Late Game — Baron-Buff = potenzieller Game-Winner",) if is_late else ()),
            ),
        )

    # Setup phase — vision + wave clear
    confidence = min(0.85, 0.78 + (0.05 if is_late else 0.0))
    if remaining <= 90:
        action = "Waves + Vision (Tri-Bush, River)"
    else:
        action = f"Waves claren, Pinks kaufen ({int(remaining)}s)"

    return Recommendation(
        text=f"Baron in {int(remaining)}s — {action}",
        severity="warn",
        category="objective",
        confidence=confidence,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="baron_take",
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Gold-Diff: {gold:+d} | Level: {levels:+.1f}",
            f"Numbers: {allies_alive}v{enemies_alive} alive",
            *(("Late Game — Setup ist kritisch",) if is_late else ()),
        ),
    )


def rule_fight_opportunity(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level fight recommendation. Fires on a clearly favorable OR
    clearly unfavorable fight score. Surfaces:
    - Overall fight-chance percentage
    - Focus target (champion to kill first + reason)
    - AoE CC warnings ("NICHT CLUSTERN")
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None

    game_time = getattr(snapshot, "game_time", 0.0)
    score = fight_score(snapshot)
    win_pct = int(win_probability(snapshot) * 100)
    allies_alive = _alive_count(allies)
    enemies_alive = _alive_count(enemies)
    numbers_diff = allies_alive - enemies_alive
    gold = _team_gold_diff(snapshot)

    # Only fire when there's a clear directional signal
    if -FIGHT_SCORE_THRESHOLD < score < FIGHT_SCORE_THRESHOLD:
        return None

    raw_events = list(getattr(snapshot, "raw_events", []) or [])
    focus = _focus_target(enemies, game_time, raw_events)
    aoe_warnings = _aoe_cc_warnings(enemies)[:2]

    reasons: list[str] = [
        f"Fight-Chance: {win_pct}% (Score {score:+.2f})",
        f"Numbers: {allies_alive}v{enemies_alive} alive",
        f"Gold-Diff: {gold:+d}",
    ]
    if focus:
        reasons.append(f"Fokus: {focus[0]} — {focus[1]}")
    for w in aoe_warnings:
        reasons.append(f"AoE-Warnung: {w}")

    if score >= FIGHT_SCORE_THRESHOLD:
        # Don't recommend engaging when we're down in numbers
        if numbers_diff < 0:
            return None
        severity = "alert" if score >= 0.55 or numbers_diff >= 2 else "warn"
        confidence = min(0.95, 0.60 + score * 0.35)
        risk = "LOW" if gold >= GOLD_LEAD_THRESHOLD else "MEDIUM"

        # Build natural-sounding main text: "Fight 74% — 5v3. Fokus Jinx. Nicht clustern (Ori)!"
        parts: list[str] = []
        if numbers_diff >= 1:
            parts.append(f"Fight {allies_alive}v{enemies_alive} ({win_pct}%)")
        else:
            parts.append(f"Fight JETZT ({win_pct}%)")
        if focus:
            parts.append(f"Fokus {focus[0]}")
        if aoe_warnings:
            # Extract champion name from "ChampName — Tag text"
            aoe_champ = aoe_warnings[0].split(" — ")[0]
            parts.append(f"Nicht clustern ({aoe_champ})!")

        return Recommendation(
            text=" — ".join(parts),
            severity=severity,
            category="tempo",
            confidence=confidence,
            risk=risk,
            ttl_s=15.0,
            kind="fight",
            reasons=tuple(reasons),
        )
    else:
        # Unfavorable fight
        confidence = min(0.90, 0.60 + abs(score) * 0.30)
        return Recommendation(
            text=f"Fights meiden ({win_pct}%) — farmen + Vision",
            severity="warn",
            category="safety",
            confidence=confidence,
            risk="HIGH",
            ttl_s=20.0,
            kind="fight_bad",
            reasons=tuple(reasons),
        )


def rule_enemy_base_exposed(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor turret(s) down → base exposed, push for GG.

    Fires when at least one lane has all three outer/inner/inhib turrets
    fallen, meaning our minions are now pushing into the base and an inhib
    kill creates super-minions. Higher-priority than lane_open.
    """
    active_team = (getattr(snapshot, "active_team", "") or "")
    if not active_team:
        return None
    full_counts = _enemy_turrets_down(snapshot, tiers=("P1", "P2", "P3"))
    exposed = [lane for lane, n in full_counts.items() if n >= 3]
    if not exposed:
        return None
    lanes_str = " + ".join(sorted(exposed))
    gold = _team_gold_diff(snapshot)
    return Recommendation(
        text=f"{lanes_str}-Inhib offen — Basis-Angriff, GG forcen!",
        severity="alert",
        category="lane",
        confidence=0.88,
        risk="LOW",
        ttl_s=90.0,
        kind="base_exposed",
        reasons=(
            f"Enemy {lanes_str}: Outer + Inner + Inhib-Turm gefallen",
            "Inhib-Kill = Super-Minions dauerhafter Pressure",
            f"Gold-Diff: {gold:+d}",
        ),
    )


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


def rule_ace_detected(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """All 5 enemies dead simultaneously — game-winning window, push NOW."""
    enemies = list(getattr(snapshot, "enemies", []) or [])
    allies = list(getattr(snapshot, "allies", []) or [])
    if len(enemies) < 5 or not allies:
        return None
    enemies_alive = _alive_count(enemies)
    if enemies_alive > 0:
        return None
    allies_alive = _alive_count(allies)
    return Recommendation(
        text=f"ACE! Alle 5 Feinde tot — PUSHEN zum GG! ({allies_alive}v0)",
        severity="alert",
        category="tempo",
        confidence=0.98,
        risk="LOW",
        ttl_s=30.0,
        kind="ace",
        reasons=(
            "ACE — alle Gegner tot",
            f"Allies alive: {allies_alive}/5",
            "Pushe Inhib + Nexus-Türme sofort!",
        ),
    )


def rule_enemy_herald_danger(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy picked up Rift Herald → tower-push wave incoming.

    Only fires within HERALD_USAGE_WINDOW_S (3 min) of pickup so the warning
    auto-expires once the herald has almost certainly been placed.
    """
    pickup = _enemy_herald_pickup(snapshot)
    if pickup is None:
        return None
    _, remaining = pickup
    return Recommendation(
        text=f"Enemy Herald ({int(remaining)}s) — TOP-Push! Ward River!",
        severity="warn",
        category="lane",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_herald",
        reasons=(
            "Gegner pickupte Rift Herald",
            f"Verbleibendes Fenster: ~{int(remaining)}s",
            "Herald → TOP/MID-Tower = frühe Gold-Plates",
            "Ward Top-River + Tri-Bush vor dem Push",
        ),
    )


def rule_ally_herald_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally picked up Rift Herald — place it within 3 minutes (B4 tempo).

    Eye of the Herald expires 180s after pickup. The rule fires for the
    full window with escalating urgency so the team doesn't waste the item:
    - >120s remaining → info "Herald platzieren!"
    - 60–120s           → warn "Herald läuft ab — JETZT platzieren!"
    - ≤60s              → alert "Herald JETZT! Nur noch Xs!"
    """
    pickup = _herald_pickup(snapshot, team="ally")
    if pickup is None:
        return None
    _, remaining = pickup
    if remaining > 120:
        return Recommendation(
            text=f"Ally Herald — platzieren! ({int(remaining)}s verbleibend)",
            severity="info",
            category="tempo",
            confidence=0.90,
            risk="LOW",
            ttl_s=remaining,
            kind="ally_herald",
            reasons=(
                f"Eye of the Herald: {int(remaining)}s bis Ablauf",
                "Herald → TOP/MID Tower = Plate-Gold + Map-Pressure",
                "Jetzt sidelaners informieren + platzieren",
            ),
        )
    if remaining > 60:
        return Recommendation(
            text=f"Herald läuft ab in {int(remaining)}s — JETZT platzieren!",
            severity="warn",
            category="tempo",
            confidence=0.93,
            risk="LOW",
            ttl_s=remaining,
            kind="ally_herald",
            reasons=(
                f"Eye of the Herald: nur noch {int(remaining)}s!",
                "Herald Ablauf = verschwendetes Objective",
                "Sofort Top- oder Mid-Tower angreifen",
            ),
        )
    return Recommendation(
        text=f"Herald JETZT! Nur noch {int(remaining)}s — sofort platzieren!",
        severity="alert",
        category="tempo",
        confidence=0.96,
        risk="LOW",
        ttl_s=remaining,
        kind="ally_herald",
        reasons=(
            f"Eye of the Herald: {int(remaining)}s — KRITISCH",
            "Herald verschwindet gleich — sofort nutzen!",
        ),
    )


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


def rule_dragon_soul_pressure(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fires for DRAGON_SOUL_SIGNAL_S seconds after the 4th dragon is secured.

    Reminds the team to capitalise on Dragon Soul before regrouping attention
    on Baron/Elder. Suppressed by any active objective-take call (which is
    already the more specific action) and by numbers_disadv."""
    ally_stacks = _drake_stack_count(snapshot)
    if ally_stacks < 4:
        return None
    events = getattr(snapshot, "raw_events", []) or []
    allies = list(getattr(snapshot, "allies", []) or [])
    if not allies:
        return None
    ids = _player_ids(allies)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    dragon_kills = sorted(
        (e for e in events if e.get("EventName") == "DragonKill" and e.get("KillerName") in ids),
        key=lambda e: float(e.get("EventTime", 0)),
    )
    if len(dragon_kills) < 4:
        return None
    soul_time = float(dragon_kills[3].get("EventTime", 0))
    age_s = game_time - soul_time
    if age_s > DRAGON_SOUL_SIGNAL_S:
        return None
    gold = _team_gold_diff(snapshot)
    return Recommendation(
        text="Dragon Soul gesichert — Group + Baron/Elder forcen!",
        severity="info",
        category="objective",
        confidence=0.82,
        risk="LOW",
        ttl_s=max(0.0, DRAGON_SOUL_SIGNAL_S - age_s),
        kind="dragon_soul",
        reasons=(
            f"Dragon Soul aktiv ({ally_stacks} Drachen)!",
            "Baron + Dragon Soul = maximale Siegchance",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_void_grubs(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Early-game Void Grub objective (Season 14+, 4:30–14:00 window).

    Tracks ally/enemy VoidGrub event counts and surfaces:
      - Enemy Hornguard (≥3 grubs) → warn to play turrets defensively
      - Ally Hornguard (≥3 grubs)  → info to press tower advantages
      - Contest phase (neither at 3) → info to keep contesting

    Note: LCDA event name is 'VoidGrub' (not 'VoidGrubKill').
    If Riot renames it in a future patch, update the EventName strings
    in _ally_grub_count / _enemy_grub_count."""
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not (VOID_GRUB_WINDOW_START_S <= game_time <= VOID_GRUB_WINDOW_END_S):
        return None
    ally_grubs = _ally_grub_count(snapshot)
    enemy_grubs = _enemy_grub_count(snapshot)
    total_killed = ally_grubs + enemy_grubs
    gold = _team_gold_diff(snapshot)
    remaining_window = max(0.0, VOID_GRUB_WINDOW_END_S - game_time)

    # Enemy Hornguard — defensive alert
    if enemy_grubs >= VOID_GRUB_HORNGUARD:
        return Recommendation(
            text=f"Gegner Hornguard ({enemy_grubs} Grubs) — Türme vorsichtig verteidigen!",
            severity="warn",
            category="safety",
            confidence=0.82,
            risk="HIGH",
            ttl_s=min(60.0, remaining_window),
            kind="enemy_hornguard",
            reasons=(
                f"Gegner hat {enemy_grubs} Void Grubs — Hornguard-Voidmites aktiv!",
                "Voidmites greifen Türme an — defensiv spielen",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Ally Hornguard — push signal
    if ally_grubs >= VOID_GRUB_HORNGUARD:
        return Recommendation(
            text=f"Hornguard aktiv ({ally_grubs} Grubs) — Türme pushen!",
            severity="info",
            category="objective",
            confidence=0.78,
            risk="LOW",
            ttl_s=min(60.0, remaining_window),
            kind="ally_hornguard",
            reasons=(
                f"Wir haben {ally_grubs} Void Grubs — Voidmites attacken Türme!",
                "Split-Push oder Group für maximalen Türm-Druck",
                f"Gold-Diff: {gold:+d}",
            ),
        )

    # Contest phase — grubs still available
    if total_killed < 6:
        needed = max(0, VOID_GRUB_HORNGUARD - ally_grubs)
        return Recommendation(
            text=f"Void Grubs — noch {needed} für Hornguard! ({int(remaining_window)}s)",
            severity="info",
            category="objective",
            confidence=0.72,
            risk="MEDIUM",
            ttl_s=min(60.0, remaining_window),
            kind="void_grub_contest",
            reasons=(
                f"Void Grubs: Wir {ally_grubs} — Gegner {enemy_grubs} (von 6 total)",
                f"{VOID_GRUB_HORNGUARD} Grubs = Hornguard (Voidmites greifen Türme an)",
                f"Fenster endet ca. 14:00 ({int(remaining_window)}s übrig)",
            ),
        )
    return None


def rule_enemy_jungler_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy jungler is dead — push/contest window (B2 contribution).

    Detects the enemy jungler via Smite spell presence. When their respawn
    timer exceeds JUNGLER_DOWN_MIN_S this rule fires:
    - alert + "take objective" when any objective spawns within 60s
    - warn + "push lane" otherwise

    TTL = respawn_timer so the card expires when they come back.
    Suppressed by numbers_disadv (we're short-handed too), ace (already
    acting on full momentum), and ally_inhib_down (defend first)."""
    enemies = list(getattr(snapshot, "enemies", []) or [])
    jungler = next((p for p in enemies if _is_jungler(p)), None)
    if jungler is None:
        return None
    respawn = float(getattr(jungler, "respawn_timer", 0.0) or 0.0)
    if respawn < JUNGLER_DOWN_MIN_S:
        return None
    champ = getattr(jungler, "champion_name", "") or "Jungler"
    gold = _team_gold_diff(snapshot)
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    objs = list(getattr(snapshot, "objectives", []) or [])
    obj_soon = any(
        (rem := o.remaining(game_time)) is not None and 0.0 <= rem <= JUNGLER_DOWN_OBJ_WINDOW_S
        for o in objs
    )
    if obj_soon:
        text = f"JUNGLER DOWN ({champ} — {int(respawn)}s) — JETZT Objective sichern!"
        severity = "alert"
    else:
        text = f"Jungler down ({champ} — {int(respawn)}s) — Lane pushen!"
        severity = "warn"
    return Recommendation(
        text=text,
        severity=severity,
        category="objective",
        confidence=0.85,
        risk="LOW",
        ttl_s=respawn,
        kind="jungler_down",
        reasons=(
            f"{champ} respawnt in {int(respawn)}s",
            "Kein Gank-Risiko — aggressiv pushen / Objective nehmen",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_enemy_dragon_soul(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy is at soul point (3 drakes) — persistent denial reminder (B3).

    Fires between drake spawns when enemy has exactly 3 stacks. Silent when
    dragon_window is about to open (within ENEMY_SOUL_POINT_HANDOFF_S) since
    that rule provides more specific "VERHINDERN" messaging, and silent once
    the enemy secures soul (4+ stacks, game-over scenario handled elsewhere).
    """
    enemy_stacks = _enemy_drake_stack_count(snapshot)
    if enemy_stacks != 3:
        return None
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    remaining = drake_obj.remaining(game_time) if drake_obj else None
    if remaining is not None and remaining <= ENEMY_SOUL_POINT_HANDOFF_S:
        return None  # dragon_window takes over
    gold = _team_gold_diff(snapshot)
    ttl = min(ENEMY_SOUL_POINT_HANDOFF_S, remaining - ENEMY_SOUL_POINT_HANDOFF_S) if remaining else 90.0
    return Recommendation(
        text="Feind bei 3 Drachen — SOUL POINT! Nächsten Drake um jeden Preis verhindern!",
        severity="warn",
        category="objective",
        confidence=0.88,
        risk="HIGH",
        ttl_s=max(30.0, ttl),
        kind="enemy_soul_point",
        reasons=(
            f"Gegner: {enemy_stacks} Drake-Stacks — ein Drache = Soul!",
            "Drake-Soul ist oft spielentscheidend — unbedingt verhindern",
            f"Gold-Diff: {gold:+d}",
        ),
    )


def rule_ally_inhib_respawning(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally inhibitor respawns soon — transition from defense to objectives (B4).

    Fires in the final ALLY_INHIB_RESPAWN_ALERT_S (60s) before the soonest
    ally inhib respawn, signaling that the defensive pressure window is closing
    and the team can plan a Baron/Dragon call.
    """
    remaining = _earliest_ally_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > ALLY_INHIB_RESPAWN_ALERT_S:
        return None
    return Recommendation(
        text=f"Ally Inhib respawnt in {int(remaining)}s — dann Objectives möglich!",
        severity="info",
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=remaining,
        kind="ally_inhib_respawning",
        reasons=(
            f"Eigener Inhibitor respawnt in {int(remaining)}s",
            "Super-Minions stoppen → Baron/Dragon-Fenster öffnet sich",
            "Ults + Wellen bereit halten",
        ),
    )


def rule_ally_inhib_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy destroyed one or more of OUR inhibitors — defensive alert (B4).

    Super-minions now spawn for the enemy in our lanes. Risk: enemy can
    siege our base towers without any effort. Correct play is wave-clear
    priority over mid-map objectives until the inhibitor respawns.
    Suppressed when numbers_disadv is also active (already showing safety rec).
    """
    count = _active_ally_inhibitors_down(snapshot)
    if count <= 0:
        return None
    label = f"{count}x" if count > 1 else "Dein"
    return Recommendation(
        text=f"{label} Inhib DOWN — Wellen clearen! Basis verteidigen!",
        severity="alert" if count >= 2 else "warn",
        category="safety",
        confidence=0.90,
        risk="HIGH",
        ttl_s=90.0,
        kind="ally_inhib_down",
        reasons=(
            f"{count} eigener Inhibitor zerstört",
            "Feind-Super-Minions spawnen in deiner Lane",
            "Wellen clearen → Nexus-Türme schützen",
        ),
    )


def rule_enemy_inhibitor_down(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """One or more enemy inhibitor buildings are dead → Super-Minions active.

    Unlike base_exposed (inhibitor turret fallen), this fires only after the
    actual inhibitor structure is destroyed, signalling super-minion pressure
    is already live.
    """
    count = _active_enemy_inhibitors_down(snapshot)
    if count <= 0:
        return None
    return Recommendation(
        text=f"{count}x Feind-Inhib DOWN — Super-Minions spawnen! Nexus-Angriff!",
        severity="alert" if count >= 2 else "warn",
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=90.0,
        kind="inhib_down",
        reasons=(
            f"{count} enemy inhibitor(s) zerstört",
            "Super-Minions = dauerhafter Minion-Pressure",
            "Pushe mit Welle für Nexus-Türme",
        ),
    )


def rule_enemy_inhib_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy inhibitor is about to respawn — push NOW before it comes back (B4).

    Inhibitors respawn 300s after being killed. Fires in the final
    INHIB_EXPIRY_ALERT_S (60s) as a last-chance push reminder. Once the
    inhib is back, the super-minion pressure ends and the siege window
    closes.

    Suppressed by numbers_disadv and ally_inhib_down — when defending
    our own base or short-handed, attacking theirs is wrong priority.
    """
    remaining = _earliest_enemy_inhib_respawn_remaining(snapshot)
    if remaining is None or remaining > INHIB_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Feind-Inhib respawnt in {int(remaining)}s — JETZT Nexus-Türme!",
        severity=severity,
        category="lane",
        confidence=0.90,
        risk="LOW",
        ttl_s=remaining,
        kind="inhib_expiring",
        reasons=(
            f"Enemy Inhibitor respawnt in {int(remaining)}s",
            "Super-Minion-Pressure endet — Fenster schließt sich",
            "Nexus-Türme jetzt angreifen oder Vorteil verlieren",
        ),
    )


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


def rule_baron_buff_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally Baron buff (Hand of Baron) is about to run out — push NOW (B4).

    The buff lasts 180s. Fires during the final BARON_BUFF_EXPIRY_ALERT_S (60s)
    as a last-chance reminder to convert waves or take a structure before the
    enhanced-minion pressure is lost. Suppressed by numbers_disadv — pushing
    into an alive enemy team while short-handed is still bad.
    """
    remaining = _ally_baron_buff_remaining(snapshot)
    if remaining is None or remaining > BARON_BUFF_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Baron-Buff läuft ab in {int(remaining)}s — JETZT pushen!",
        severity=severity,
        category="tempo",
        confidence=0.92,
        risk="LOW",
        ttl_s=remaining,
        kind="baron_buff_expiring",
        reasons=(
            f"Hand of Baron endet in {int(remaining)}s",
            "Supercharged Minions — letztes Push-Fenster nutzen",
            "Nexus-Türme jetzt oder Buff verschwendet",
        ),
    )


def rule_enemy_baron_buff(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy has active Baron Nashor buff — defend our base (B4).

    Fires for the full 180s buff duration. Severity escalates in the
    final 60s when the buff is expiring and a counter-engage window opens:
    - remaining > 60s → warn "defend base"
    - remaining ≤ 60s → alert "counter-engage window" (they're losing the buff)

    NOT suppressed by numbers_disadv — knowing the enemy has Baron is
    still critical defensive information when short-handed.
    """
    remaining = _enemy_baron_buff_remaining(snapshot)
    if remaining is None:
        return None
    if remaining > BARON_BUFF_EXPIRY_ALERT_S:
        return Recommendation(
            text=f"Feind Baron-Buff ({int(remaining)}s) — Basis sichern! Kein Mid-Map!",
            severity="warn",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=remaining,
            kind="enemy_baron_buff",
            reasons=(
                f"Gegner hat Hand of Baron — {int(remaining)}s verbleibend",
                "Feind-Super-Minions + Buff — Basis-Türme defensiv halten",
                "Kein Objective contest — erst Baron-Buff ablaufen lassen",
            ),
        )
    return Recommendation(
        text=f"Feind Baron-Buff läuft ab in {int(remaining)}s — Konter-Engage Fenster!",
        severity="alert",
        category="tempo",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_baron_buff",
        reasons=(
            f"Feind Baron-Buff endet in {int(remaining)}s",
            "Buff-Vorteil endet — Konter-Engage wird möglich",
            "Wellen clearen + Ults bereit → dann Engage",
        ),
    )


def rule_enemy_elder_buff(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemy has Elder Drake buff — do NOT fight, stall until it expires (B4).

    Elder buff lasts 150s and grants execute damage (true damage at low HP).
    Fighting while the enemy has Elder is almost always fatal. Two phases:
    - >30s remaining → alert: stall, do NOT engage
    - ≤30s remaining → alert: buff expires soon, counter-engage window opens
    """
    remaining = _enemy_elder_buff_remaining(snapshot)
    if remaining is None:
        return None
    if remaining > 30:
        return Recommendation(
            text=f"FEIND ELDER-BUFF ({int(remaining)}s) — NICHT KÄMPFEN! Stallen!",
            severity="alert",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=remaining,
            kind="enemy_elder_buff",
            reasons=(
                f"Gegner hat Elder-Buff — {int(remaining)}s verbleibend",
                "Elder Execute = True Damage — jeder Fight tödlich!",
                "Wellen clearen + Basis sichern → Buff ablaufen lassen",
            ),
        )
    return Recommendation(
        text=f"Feind Elder-Buff endet in {int(remaining)}s — Konter-Engage Fenster!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="MEDIUM",
        ttl_s=remaining,
        kind="enemy_elder_buff",
        reasons=(
            f"Elder-Buff endet in {int(remaining)}s",
            "Execute-Vorteil endet — Engage wird sicherer",
            "Ults bereit halten → sofort Engage wenn Buff weg",
        ),
    )


def rule_elder_buff_expiring(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Ally Elder Drake buff about to expire — push NOW (B4).

    Elder buff lasts 150s; fires in the final ELDER_BUFF_EXPIRY_ALERT_S (60s).
    Elder-buffed executes (true damage at low HP) make teamfights decisive —
    the team should be grouping and fighting, not farming waves.
    """
    remaining = _ally_elder_buff_remaining(snapshot)
    if remaining is None or remaining > ELDER_BUFF_EXPIRY_ALERT_S:
        return None
    severity = "alert" if remaining <= 30 else "warn"
    return Recommendation(
        text=f"Elder-Buff läuft ab in {int(remaining)}s — JETZT teamfighten!",
        severity=severity,
        category="tempo",
        confidence=0.93,
        risk="LOW",
        ttl_s=remaining,
        kind="elder_buff_expiring",
        reasons=(
            f"Elder Drake Buff endet in {int(remaining)}s",
            "Elder Execute = True Damage bei niedrigem HP — Fights gewinnen",
            "Letztes Fenster mit Elder-Vorteil — jetzt gruppieren!",
        ),
    )


def rule_enemy_flash_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Alert when one or more enemies have Flash on cooldown (B2 — engage window).

    Requires a SpellTracker with user-tracked spell casts. Fires when at least
    one tracked enemy flash has more than FLASH_DOWN_ALERT_S remaining so the
    alert is still actionable. Suppressed by _suppress_dominated when the team
    is behind (numbers_disadv) — flash-down is an opportunity only when safe.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    flashes_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Flash":
                continue
            name = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Flash", game_time
            )
            if remaining > FLASH_DOWN_ALERT_S:
                flashes_down.append((name, remaining))
            break  # each enemy has at most one Flash

    if not flashes_down:
        return None

    count = len(flashes_down)
    names = ", ".join(n for n, _ in flashes_down[:3])
    min_remaining = min(r for _, r in flashes_down)

    if count == 1:
        name, remaining = flashes_down[0]
        text = f"Flash down: {name} ({int(remaining)}s)"
    else:
        text = f"{count}× Flash down — Engage-Fenster!"

    return Recommendation(
        text=text,
        severity="warn",
        category="tempo",
        confidence=0.85,
        risk="MEDIUM",
        ttl_s=min_remaining,
        kind="flash_down",
        reasons=(
            f"{names} ohne Flash",
            f"Flash bereit in ~{int(min_remaining)}s",
            "Gutes Fenster zum Engagen oder Diven",
        ),
    )


def rule_enemy_tp_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when one or more enemies have Teleport on cooldown (B2).

    TP down blocks global rotations — the enemy can't react to your
    side-lane pressure or TP to save a collapsing teamfight. Fires when
    remaining CD > TP_DOWN_ALERT_S (90s) so the info is still actionable.
    Suppressed by numbers_disadv — don't split when short-handed.

    Severity scales with count:
    - 1 TP down → info (single-person advisory)
    - 2+ TP down → warn (major tempo window)
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    tps_down: list[tuple[str, float]] = []
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            if spell is None or getattr(spell, "name", "") != "Teleport":
                continue
            name = (
                getattr(enemy, "summoner_name", "")
                or getattr(enemy, "champion_name", "")
            )
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), "Teleport", game_time,
            )
            if remaining > TP_DOWN_ALERT_S:
                tps_down.append((name, remaining))
            break

    if not tps_down:
        return None

    count = len(tps_down)
    names = ", ".join(n for n, _ in tps_down[:3])
    min_remaining = min(r for _, r in tps_down)
    severity = "warn" if count >= 2 else "info"

    if count == 1:
        name, remaining = tps_down[0]
        text = f"TP down: {name} ({int(remaining)}s) — kein Flank-TP!"
    else:
        text = f"{count}× TP down ({int(min_remaining)}s) — keine globale Rotation!"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=0.88,
        risk="LOW",
        ttl_s=min_remaining,
        kind="tp_down",
        reasons=(
            f"{names} ohne Teleport",
            f"TP bereit in ~{int(min_remaining)}s",
            "Kein TP = kein globaler Eingriff — Side-Lanes frei!",
        ),
    )


def rule_enemy_combat_spell_down(
    snapshot: "LcdaSnapshot",
    spell_tracker: "SpellTracker",
) -> Recommendation | None:
    """Advisory when enemy has a tracked combat summoner spell on CD (B2).

    Covers Exhaust, Heal, Ignite, Barrier, and Cleanse — spells that
    directly affect trade outcomes. Each has a specific tactical message:
    - Exhaust down → enemy can't kite/reduce your carry
    - Heal down → no sustain/movement speed boost for ADC
    - Ignite down → no kill threat; trades are safer
    - Barrier/Cleanse down → burst/CC window

    Fires when remaining CD > COMBAT_SPELL_ALERT_S (60s). Groups multiple
    down-spells into one card to avoid flooding the overlay. Severity is
    always "info" — these are advisory notes, not urgent signals.
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not enemies or not game_time:
        return None

    _SPELL_HINTS: dict[str, str] = {
        "Exhaust":  "kein Exhaust — Carry kann all-in gehen",
        "Heal":     "kein Heal — ADC hat keine Sustain",
        "Ignite":   "kein Ignite — kein Kill-Threat in Lane",
        "Barrier":  "kein Barrier — Burst-Fenster!",
        "Cleanse":  "kein Cleanse — CC trifft sicher",
    }

    down: list[tuple[str, str, float]] = []  # (name, spell, remaining)
    for enemy in enemies:
        for spell in (getattr(enemy, "spell_one", None), getattr(enemy, "spell_two", None)):
            spell_name = getattr(spell, "name", "") if spell is not None else ""
            if spell_name not in _COMBAT_SPELLS:
                continue
            summoner = getattr(enemy, "summoner_name", "") or getattr(enemy, "champion_name", "")
            remaining = spell_tracker.remaining(
                getattr(enemy, "summoner_name", ""), spell_name, game_time,
            )
            if remaining > COMBAT_SPELL_ALERT_S:
                down.append((summoner, spell_name, remaining))
            break

    if not down:
        return None

    min_remaining = min(r for _, _, r in down)

    if len(down) == 1:
        name, spell_name, remaining = down[0]
        hint = _SPELL_HINTS.get(spell_name, f"kein {spell_name}")
        text = f"{spell_name} down: {name} ({int(remaining)}s) — {hint}"
    else:
        summary = ", ".join(f"{s}({n})" for n, s, _ in down[:3])
        text = f"Spells down: {summary}"

    return Recommendation(
        text=text,
        severity="info",
        category="tempo",
        confidence=0.83,
        risk="LOW",
        ttl_s=min_remaining,
        kind="combat_spell_down",
        reasons=tuple(
            f"{s} down: {n} — {_SPELL_HINTS.get(s, s)} ({int(r)}s)"
            for n, s, r in down
        ),
    )


def rule_fight_window_closing(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Numbers advantage that is about to disappear because a dead enemy
    respawns within FIGHT_WINDOW_CLOSING_S seconds.

    Complements rule_ace_detected (fires while all are dead) and
    rule_numbers_advantage (fires on a sustained lead). This rule fires
    during the transition: we're still ahead, but the clock is running.
    Suppressed by ace (which is already urging the push).
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return None

    # Require live respawn data — fall back gracefully if LCDA omits it.
    has_enemy_respawn = any(
        getattr(e, "respawn_timer", None) is not None for e in enemies
    )
    if not has_enemy_respawn:
        return None

    allies_alive = _alive_count(allies, default_to_full_team=False)
    enemies_alive = _alive_count(enemies, default_to_full_team=False)
    if allies_alive <= enemies_alive:
        return None

    # Find the enemy whose respawn is most imminent (but not yet alive).
    imminent = [
        e for e in enemies
        if not getattr(e, "is_alive", True)
        and 0 < getattr(e, "respawn_timer", 0.0) <= FIGHT_WINDOW_CLOSING_S
    ]
    if not imminent:
        return None

    soonest = min(imminent, key=lambda e: getattr(e, "respawn_timer", 99.0))
    timer = int(getattr(soonest, "respawn_timer", 1.0))
    name = (
        getattr(soonest, "champion_name", "")
        or getattr(soonest, "summoner_name", "")
        or "?"
    )

    return Recommendation(
        text=f"Jetzt pushen — {name} zurück in {timer}s!",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="LOW",
        ttl_s=float(timer + 3),
        kind="window_closing",
        reasons=(
            f"{allies_alive}v{enemies_alive} alive",
            f"{name} respawnt in {timer}s — Fenster schließt sich",
        ),
    )


def rule_power_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Alert when the active player just crossed a power-spike threshold
    (level 6/11/16 or first/second/third legendary item).

    The ``PowerSpikePanel`` in the main overlay hides during gameplay —
    this rule surfaces the same signal in the floating RecommendationPanel
    so the alert is visible while a game is running.

    TTL is short (20-30s) so the card expires before the window closes.
    """
    spikes = getattr(snapshot, "new_spikes", []) or []
    if not spikes:
        return None
    # Level spikes (6/11/16) outweigh item spikes; higher value wins within each kind.
    spike = max(spikes, key=lambda s: (1 if getattr(s, "kind", "") == "level" else 0, getattr(s, "value", 0)))
    kind = getattr(spike, "kind", "")
    value = getattr(spike, "value", 0)
    label = getattr(spike, "label", "Power Spike")
    detail = getattr(spike, "detail", "")

    text = f"{label} — {detail}" if detail else label

    if kind == "level" and value == 6:
        severity, ttl_s = "alert", 20.0
    elif kind == "level":
        severity, ttl_s = "warn", 25.0
    elif kind == "items" and value == 2:
        severity, ttl_s = "warn", 30.0
    else:
        severity, ttl_s = "info", 30.0

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=1.0,
        risk="LOW",
        ttl_s=ttl_s,
        kind="power_spike",
        reasons=(detail,) if detail else (),
    )


def rule_enemy_item_spike(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Warn when an enemy champion just completed their 1st/2nd/3rd legendary
    item. The most dangerous spike (highest legendary count) surfaces first
    so the player knows which enemy is now scarier.

    2nd legendary is the critical threshold for carries — that's typically
    their mid-game power peak. 1st legendary fires as info only.
    """
    spikes = getattr(snapshot, "enemy_spikes", []) or []
    if not spikes:
        return None

    # Most dangerous spike first (highest legendary count, then alphabetical).
    top = max(spikes, key=lambda s: (getattr(s, "legendary_count", 0), getattr(s, "champion_name", "")))
    champ = getattr(top, "champion_name", "Gegner")
    count = getattr(top, "legendary_count", 1)

    if count >= 2:
        severity, ttl_s = "warn", 30.0
        text = f"{champ} hat {count}. Item — Vorsicht, starker Spike!"
    else:
        severity, ttl_s = "info", 25.0
        text = f"{champ} hat 1. Item fertig"

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=0.90,
        risk="MEDIUM" if count >= 2 else "LOW",
        ttl_s=ttl_s,
        kind="enemy_spike",
        reasons=(f"{champ}: {count}. Legendary Item abgeschlossen",),
    )


# Game-phase boundaries used by tilt-rule messaging. These are
# coaching cutoffs, not hard mechanical phases — late-game advice
# (group 5, no splits) gets dangerous before 25:00 in solo-queue.
_TILT_LANE_PHASE_END_S: float = 840.0    # 14:00 — first item, lane priority shifts
_TILT_MID_GAME_END_S: float = 1500.0     # 25:00 — Baron + late-game grouping


def _tilt_phase_advice(game_time: float) -> str:
    """One-liner of *what to do during the next walk-back* given the
    current game phase. Returned advice is concrete, not motivational."""
    if game_time <= _TILT_LANE_PHASE_END_S:
        return "Welle unter Turm freezen, Jungler pingen, kein 1v1"
    if game_time <= _TILT_MID_GAME_END_S:
        return "Mit Team gruppieren, kein Side-Lane, Vision setzen"
    return "Death-Timer 50s+ — niemals alleine zeigen, nur 5er Plays"


# ─── Recall-window thresholds (B5 — Recommendation Service) ──────────────────
# These match the way pros actually think about resource state, not raw HP/mana
# numbers. Tuned conservatively: false positives are worse than missed calls
# because the player will mute a noisy assistant within one game.

HP_CRITICAL_PCT: float = 0.30   # below this you die to a single combo
HP_LOW_PCT: float = 0.50        # below this, trades aren't safe
MANA_DEPLETED_PCT: float = 0.20 # below this, you can't trade or escape
MANA_LOW_PCT: float = 0.30      # below this, you're at most 1 ability away from dry

# Gold tiers — generic component thresholds the player can map to their build.
GOLD_BACK_WORTH: float = 1100.0       # Sheen / Tear / first boots
GOLD_COMPONENT_SPIKE: float = 1300.0  # Lost Chapter / Caulfield's tier
GOLD_LARGE_SPIKE: float = 1600.0      # Pickaxe / BF Sword tier

# Recall coaching is most valuable in lane + early mid-game. After 20:00,
# back timing is dictated by team rotations, not personal resources.
RECALL_PHASE_END_S: float = 1200.0    # 20:00


# Hysteresis state for the recall rule — process-wide dedup so each
# tier doesn't re-fire every 2 s while its trigger condition persists.
# All four tiers re-arm only after the player crosses the corresponding
# rearm threshold (HP > 35 %, mana > 30 %, gold spent below threshold).
HP_RECALL_REARM_PCT: float = 0.35
MANA_RECALL_REARM_PCT: float = 0.30
GOLD_RECALL_REARM_BUFFER: float = 200.0  # gold must drop this far below threshold



def rule_recall_check(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Recall-window coaching driven by HP %, mana %, and gold (Charter B5).

    Picks at most one of four signals, in priority order:

    1. **Critical HP** (alert) — HP < 30 %; surfaces "back NOW" regardless
       of gold or game phase. Any next interaction kills you.

    2. **Resource depleted + back-worth gold** (warn) — HP < 50 % OR mana
       < 25 %, AND gold ≥ 1100. The classic "you need a reset, and you
       have value to bank" signal. Pros recall here every time.

    3. **Pure gold opportunity** (info) — gold ≥ 1300 in lane phase, even
       at full HP. The next trip back is worth a real spike; don't sit
       on uncashed gold while losing tempo.

    4. **Mana check** (info) — mana < 20 % in lane phase. Tells the
       player they're now in their opponent's all-in window, and to
       freeze the wave / use Doran's regen until mana is back.

    Skipped while dead (hp_pct ≤ 0). Does **not** fire after 20:00 except
    for tier 1 (critical HP); recall timing past 20:00 is dictated by
    team rotation, not personal resources.
    """
    state = getattr(snapshot, "active_combat", None)
    if state is None:
        return None
    hp_pct = float(getattr(state, "hp_pct", 1.0))
    mana_pct = float(getattr(state, "mana_pct", 1.0))
    gold = float(getattr(state, "gold", 0.0))
    is_mana_user = bool(getattr(state, "is_mana_user", False))
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    h = _RECALL_HYSTERESIS

    # Dead players get no advice — they can't act on it before respawn.
    if hp_pct <= 0.0:
        # Reset hysteresis on death — next life starts fresh.
        h.reset()
        return None

    # Re-arm each tier once its rearm threshold is crossed. Without this
    # the rules fire every snapshot tick while their trigger condition
    # persists, producing dozens of identical recs per game.
    if hp_pct >= HP_RECALL_REARM_PCT:
        h.critical = True
    if hp_pct >= HP_LOW_PCT and (not is_mana_user or mana_pct >= MANA_LOW_PCT):
        h.resource = True
    if gold < GOLD_COMPONENT_SPIKE - GOLD_RECALL_REARM_BUFFER:
        h.gold = True
    if not is_mana_user or mana_pct >= MANA_RECALL_REARM_PCT:
        h.mana = True

    # Tier 1 — Critical HP. Fires once per "below 30 %" episode.
    if hp_pct < HP_CRITICAL_PCT and not h.critical:
        return None
    if hp_pct < HP_CRITICAL_PCT:
        h.critical = False
        pct = int(hp_pct * 100)
        return Recommendation(
            text=f"{pct}% HP — RECALL JETZT, nächster Trade tötet dich",
            severity="alert",
            category="safety",
            confidence=0.95,
            risk="HIGH",
            ttl_s=15.0,
            kind="recall_critical",
            reasons=(
                f"HP: {pct}%",
                f"Gold dabei: {int(gold)}g",
                "Jeder Skillshot / Auto = Tod",
            ),
        )

    # Tier 2 — Resource depleted + back-worth gold (warn).
    resource_low = hp_pct < HP_LOW_PCT or (is_mana_user and mana_pct < MANA_LOW_PCT)
    if (
        resource_low and gold >= GOLD_BACK_WORTH
        and game_time <= RECALL_PHASE_END_S
        and h.resource
    ):
        h.resource = False
        triggers: list[str] = []
        if hp_pct < HP_LOW_PCT:
            triggers.append(f"HP {int(hp_pct*100)}%")
        if is_mana_user and mana_pct < MANA_LOW_PCT:
            triggers.append(f"Mana {int(mana_pct*100)}%")
        spike_tier = (
            "Large Item" if gold >= GOLD_LARGE_SPIKE
            else "Component Spike" if gold >= GOLD_COMPONENT_SPIKE
            else "Component"
        )
        return Recommendation(
            text=f"Recall lohnt — {' + '.join(triggers)}, {int(gold)}g für {spike_tier}",
            severity="warn",
            category="safety",
            confidence=0.85,
            risk="MEDIUM",
            ttl_s=20.0,
            kind="recall_resource",
            reasons=(
                *triggers,
                f"Gold: {int(gold)}g (≥{int(GOLD_BACK_WORTH)}g back-worth)",
                "Reset-Tempo > vor-pushen + halb-tot bleiben",
            ),
        )

    # Tier 3 — Pure gold opportunity (info). Lane phase only.
    if (
        gold >= GOLD_COMPONENT_SPIKE
        and game_time <= RECALL_PHASE_END_S
        and h.gold
    ):
        h.gold = False
        return Recommendation(
            text=f"{int(gold)}g — Recall-Fenster, Component-Spike kaufen + sicher zurück",
            severity="info",
            category="tempo",
            confidence=0.70,
            risk="LOW",
            ttl_s=20.0,
            kind="recall_gold",
            reasons=(
                f"Gold: {int(gold)}g (≥{int(GOLD_COMPONENT_SPIKE)}g Component-Spike)",
                f"HP {int(hp_pct*100)}% — sicherer Reset möglich",
            ),
        )

    # Tier 4 — Mana check (info). Lane phase only, mana users only.
    if (
        is_mana_user and mana_pct < MANA_DEPLETED_PCT
        and game_time <= RECALL_PHASE_END_S
        and h.mana
    ):
        h.mana = False
        return Recommendation(
            text=f"Mana {int(mana_pct*100)}% — Gegner-All-In-Fenster offen, Welle freezen + warten",
            severity="info",
            category="safety",
            confidence=0.65,
            risk="MEDIUM",
            ttl_s=15.0,
            kind="mana_check",
            reasons=(
                f"Mana: {int(mana_pct*100)}% (<{int(MANA_DEPLETED_PCT*100)}%)",
                "Kein Trade-Antwort verfügbar — defensive Position",
            ),
        )

    return None


# ─── Skill-point unspent thresholds ──────────────────────────────────────────
# Pros tap their level-up keybind in ~1 second. After 60 seconds of game
# time the wave has hit and any unspent point is a real miss. We gate on
# HP because nagging during a trade/teamfight is worse than missing the call.
SKILL_POINT_GAME_TIME_MIN_S: float = 60.0
SKILL_POINT_HP_GATE_PCT: float = 0.50   # below 50% → in danger, don't nag


def rule_unspent_skill_points(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "you have an unspent skill point" — the cheapest meaningful
    coaching call in the game (Charter B5 — micro-coaching).

    Detection: ``unspent_skill_points`` is recomputed every tick from
    ``activePlayer.abilities`` vs player level. Fire info-level when:
      * unspent ≥ 1
      * game_time ≥ 60 s (game-start grace — first wave hasn't crashed yet)
      * hp_pct ≥ 50 % (below this the player is in a trade; nagging
        about a skill-up icon while they're trying to survive is worse
        than missing the cue)
      * player is alive (hp_pct > 0)

    Solo-queue routinely forgets skill points mid-fight or right after a
    kill confirmation. Pros never miss this. Externalising the cue
    closes one of the most frequent skill-cap micro-mistakes.
    """
    state = getattr(snapshot, "active_combat", None)
    if state is None:
        return None
    unspent = int(getattr(state, "unspent_skill_points", 0))
    if unspent <= 0:
        return None
    hp_pct = float(getattr(state, "hp_pct", 1.0))
    if hp_pct <= 0.0:
        return None  # dead — no skill cast possible
    if hp_pct < SKILL_POINT_HP_GATE_PCT:
        return None  # in active combat, don't distract from trade decisions
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if game_time < SKILL_POINT_GAME_TIME_MIN_S:
        return None  # first wave hasn't even hit yet

    plural = "Punkte" if unspent > 1 else "Punkt"
    return Recommendation(
        text=f"{unspent} Skill-{plural} offen — Q / W / E / R upgraden",
        severity="info",
        category="lane",
        confidence=0.95,
        risk="LOW",
        ttl_s=10.0,
        kind="skill_point_unspent",
        reasons=(
            f"{unspent} ungenutzte Skill-Punkte",
            "Skill-Up = freier DMG / Sustain / Mobility — kein Grund zu warten",
        ),
    )


# ─── Bounty awareness thresholds ─────────────────────────────────────────────
# Riot's actual bounty schedule (as of patch 14.x):
#   3 unanswered kills → +150g shutdown
#   4-5 unanswered    → +200-300g
#   6-7 unanswered    → +400-500g
#   8+               → +500g (capped) — "Legendary"
# League's announcer terms anchor the messages so the user immediately
# recognizes "oh that's the Killing Spree / Unstoppable / Godlike threshold".
BOUNTY_TIER_INFO_S: int = 3      # Killing Spree
BOUNTY_TIER_WARN_S: int = 5      # Unstoppable
BOUNTY_TIER_GODLIKE_S: int = 7   # Godlike+


def rule_active_bounty(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "you have a bounty on your head" once per escalation tier.

    The mirror image of ``tilt.bounty_lost`` — that modifier fires *after*
    the bounty was given to the enemy. This rule fires *before*: while
    the player still has the streak, so they can adjust their risk profile
    in time. Pros switch to "play with team, no flanks, ward around me,
    recall early at low HP" immediately. Solo queue does not.

    Three tiers, fires once each per life:
    * 3-4 streak — info, "Killing Spree (+150g)"
    * 5-6 streak — warn, "Unstoppable (+300g)"
    * 7+ streak  — warn, "Godlike (+500g)"

    Hysteresis is per-life: ``_BOUNTY_HYSTERESIS.last_seen_deaths`` flips
    to the active player's current death count, so any death (which
    nukes the bounty) re-arms every tier for the next streak.
    """
    active = _active_player(snapshot)
    if active is None:
        return None

    # Reset on death — bounty is wiped, the next streak earns its own messages.
    deaths = int(getattr(active, "deaths", 0) or 0)
    h = _BOUNTY_HYSTERESIS
    if deaths > h.last_seen_deaths:
        h.reset()
        h.last_seen_deaths = deaths

    streak = _kill_streak(active, list(getattr(snapshot, "raw_events", []) or []))
    if streak < BOUNTY_TIER_INFO_S:
        return None

    # Pick the highest tier we haven't yet announced this life.
    if streak >= BOUNTY_TIER_GODLIKE_S:
        tier = BOUNTY_TIER_GODLIKE_S
    elif streak >= BOUNTY_TIER_WARN_S:
        tier = BOUNTY_TIER_WARN_S
    else:
        tier = BOUNTY_TIER_INFO_S
    if tier <= h.last_fired_tier:
        return None  # already announced this tier or higher this life
    h.last_fired_tier = tier

    if tier == BOUNTY_TIER_GODLIKE_S:
        text = (
            f"GODLIKE ({streak}-Streak, +500g Bounty) — "
            "wie Carry spielen: hinten, mit Frontline, kein Engage"
        )
        severity, ttl_s, confidence, risk = "warn", 35.0, 0.92, "HIGH"
    elif tier == BOUNTY_TIER_WARN_S:
        text = (
            f"UNSTOPPABLE ({streak}-Streak, +300g Bounty) — "
            "KEIN Solo-Play, Vision um dich, früh recallen bei Low-HP"
        )
        severity, ttl_s, confidence, risk = "warn", 30.0, 0.88, "MEDIUM"
    else:
        text = (
            f"Killing Spree ({streak}-Streak, +150g Bounty) — "
            "mit Team gruppieren, keine 1v1, Vision-Pressure setzen"
        )
        severity, ttl_s, confidence, risk = "info", 25.0, 0.80, "MEDIUM"

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="active_bounty",
        reasons=(
            f"Killstreak: {streak} (kein Tod seit Streak-Start)",
            "Riot Bounty-System: ab 3 Kills extra Gold beim Töten",
            "Mehr Vision + weniger Solo = Streak halten = Spiel gewinnen",
        ),
    )


def rule_enemy_bounty(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "ENEMY X has a bounty — focus them" once per escalation tier
    per enemy life (Charter B5 — focus-call coaching).

    Mirror image of ``rule_active_bounty`` from the offensive side. Pros call
    out a fed enemy in TS/comms before every fight: "Jinx is shutdown,
    we kill her first". Solo queue picks the closest target, not the
    most valuable one. The +150g/+300g/+500g shutdown bounties make the
    fed enemy worth 2-3 normal kills; missing this is the single
    biggest mid-game throw.

    Per-enemy hysteresis: each enemy's fired_tier resets on their death,
    so a respawning carrier earns a fresh announcement when they get
    back on their next streak.

    Tier picking: scan all alive enemies, find the one with the highest
    streak whose tier exceeds their last announced tier, and announce
    for that one. If multiple enemies are tied, pick by champion-name
    sort order (deterministic).
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not enemies:
        return None
    events = list(getattr(snapshot, "raw_events", []) or [])
    h = _ENEMY_BOUNTY_HYSTERESIS

    # Phase 1 — update per-enemy death state. Any death wipes their
    # bounty so we re-arm every tier for them.
    for e in enemies:
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        deaths = int(getattr(e, "deaths", 0) or 0)
        prev = h.last_seen_deaths.get(name, 0)
        if deaths > prev:
            h.last_fired_tier[name] = 0
            h.last_seen_deaths[name] = deaths
        elif name not in h.last_seen_deaths:
            h.last_seen_deaths[name] = deaths

    # Phase 2 — pick the most-actionable enemy: highest streak among
    # those whose current tier is unannounced.
    best: tuple[int, int, str, object] | None = None  # (tier, streak, name, player)
    for e in enemies:
        if not getattr(e, "is_alive", True):
            continue
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        streak = _kill_streak(e, events)
        if streak < BOUNTY_TIER_INFO_S:
            continue
        if streak >= BOUNTY_TIER_GODLIKE_S:
            tier = BOUNTY_TIER_GODLIKE_S
        elif streak >= BOUNTY_TIER_WARN_S:
            tier = BOUNTY_TIER_WARN_S
        else:
            tier = BOUNTY_TIER_INFO_S
        if tier <= h.last_fired_tier.get(name, 0):
            continue
        # Tie-break by streak then alphabetical for determinism.
        candidate = (tier, streak, name, e)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, streak, name, _ = best
    h.last_fired_tier[name] = tier

    if tier == BOUNTY_TIER_GODLIKE_S:
        text = (
            f"{name} GODLIKE ({streak}-Streak, +500g Shutdown) — "
            "Hunten oder Map abgeben. Pick mit Team setzen."
        )
        severity, ttl_s, confidence, risk = "warn", 35.0, 0.92, "MEDIUM"
    elif tier == BOUNTY_TIER_WARN_S:
        text = (
            f"{name} UNSTOPPABLE ({streak}-Streak, +300g Shutdown) — "
            "Pick auf {name} setzen, Jungler pingen, CC bereit."
        ).replace("{name}", name)
        severity, ttl_s, confidence, risk = "warn", 30.0, 0.86, "MEDIUM"
    else:
        text = (
            f"{name} Killing Spree ({streak}-Streak, +150g Shutdown) — "
            f"Fokus in Fights, Vision auf ihrer Seite."
        )
        severity, ttl_s, confidence, risk = "info", 25.0, 0.78, "LOW"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="enemy_bounty",
        reasons=(
            f"{name} hat {streak} Kills ohne Tod",
            "Riot Bounty: ab 3 Kills extra Gold beim Töten",
            "Shutdown = doppelter Kill-Wert + Tempo-Reset",
        ),
    )


# Position-specific protect-the-carry advice. What "support the carry"
# actually means depends on which lane they play.
_ALLY_PROTECT_ADVICE: dict[str, str] = {
    "TOP":     "Top-Side Vision, TP-Engages für Top, Welle für Top freihalten",
    "JUNGLE":  "Jungle wardēn (river + buffs), Counter-Gank-Pressure",
    "MIDDLE":  "Mid-Roams unterstützen, Welle für Mid freihalten, Vision",
    "BOTTOM":  "Bot-Side stacken (Drachen), Engages mit CC, kein 4v5",
    "UTILITY": "Bot stacken, Vision für Bot-Plays, Engages koordinieren",
}


def rule_ally_bounty(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "ALLY X has a streak — protect-the-carry" once per tier per
    ally life (Charter B5 — protect-the-carry coaching).

    Third leg of the bounty matrix:
      * rule_active_bounty  — "you have bounty"            (defensive)
      * rule_enemy_bounty   — "enemy has bounty, focus"   (offensive)
      * rule_ally_bounty    — "ally has bounty, protect"  (supportive)

    Pros pivot the entire team strategy when an ally is fed:
      * Give the carrier side-lane control (split-push enablement)
      * Ward the side they're playing
      * Don't 4v5 — wait for them
      * Buff-share (red/blue to the carrier)
      * Stay close in mid/late for peel

    Solo queue routinely abandons the fed ally to splitpush alone, then
    fights 4v5 without them. Externalising this signal closes that gap.

    Active player is excluded — their own streak is handled by
    rule_active_bounty with different (defensive) messaging.
    """
    allies = list(getattr(snapshot, "allies", []) or [])
    if not allies:
        return None
    active_summoner = str(getattr(snapshot, "active_summoner", "") or "")
    events = list(getattr(snapshot, "raw_events", []) or [])
    h = _ALLY_BOUNTY_HYSTERESIS

    # Phase 1 — refresh per-ally death state.
    for a in allies:
        if str(getattr(a, "summoner_name", "")) == active_summoner:
            continue
        name = str(getattr(a, "champion_name", "") or "")
        if not name:
            continue
        deaths = int(getattr(a, "deaths", 0) or 0)
        prev = h.last_seen_deaths.get(name, 0)
        if deaths > prev:
            h.last_fired_tier[name] = 0
            h.last_seen_deaths[name] = deaths
        elif name not in h.last_seen_deaths:
            h.last_seen_deaths[name] = deaths

    # Phase 2 — pick the most-actionable ally (highest unannounced tier).
    best: tuple[int, int, str, object] | None = None
    for a in allies:
        if str(getattr(a, "summoner_name", "")) == active_summoner:
            continue
        if not getattr(a, "is_alive", True):
            continue
        name = str(getattr(a, "champion_name", "") or "")
        if not name:
            continue
        streak = _kill_streak(a, events)
        if streak < BOUNTY_TIER_INFO_S:
            continue
        if streak >= BOUNTY_TIER_GODLIKE_S:
            tier = BOUNTY_TIER_GODLIKE_S
        elif streak >= BOUNTY_TIER_WARN_S:
            tier = BOUNTY_TIER_WARN_S
        else:
            tier = BOUNTY_TIER_INFO_S
        if tier <= h.last_fired_tier.get(name, 0):
            continue
        candidate = (tier, streak, name, a)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, streak, name, ally = best
    h.last_fired_tier[name] = tier

    position = str(getattr(ally, "position", "") or "")
    advice = _ALLY_PROTECT_ADVICE.get(position, "Carry-Pflege, kein Solo-Engage ohne sie")

    if tier == BOUNTY_TIER_GODLIKE_S:
        text = (
            f"{name} GODLIKE ({streak}-Streak) — Win-Condition, ALLE Plays um {name}. "
            f"{advice}"
        )
        severity, ttl_s, confidence, risk = "warn", 35.0, 0.92, "MEDIUM"
    elif tier == BOUNTY_TIER_WARN_S:
        text = (
            f"{name} UNSTOPPABLE ({streak}-Streak) — Protect-the-Carry-Modus. "
            f"{advice}"
        )
        severity, ttl_s, confidence, risk = "warn", 30.0, 0.86, "MEDIUM"
    else:
        text = (
            f"{name} Killing Spree ({streak}-Streak) — peelen + Vision. "
            f"{advice}"
        )
        severity, ttl_s, confidence, risk = "info", 25.0, 0.78, "LOW"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="ally_bounty",
        reasons=(
            f"{name} hat {streak} Kills ohne Tod (Position: {position or 'unbekannt'})",
            "Fed Ally = Team-Win-Condition, Plays um sie aufbauen",
            "Kein 4v5 ohne Carry — warten lohnt fast immer",
        ),
    )


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


def _team_id_set(players: list) -> set[str]:
    """Build the set of identifiers for a team (summoner_name + champion_name
    of every member). Used by rule_first_blood to decide which side killed."""
    ids: set[str] = set()
    for p in players:
        sn = str(getattr(p, "summoner_name", "") or "")
        cn = str(getattr(p, "champion_name", "") or "")
        if sn:
            ids.add(sn)
        if cn:
            ids.add(cn)
    return ids


def rule_first_blood(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """First-Blood awareness — fires once when the first ChampionKill is
    detected (Charter B5 — early-game momentum coaching).

    First Blood gives 300g base + 100g shutdown = 400g to the killer.
    More importantly, it sets the lane-priority dynamic for the next
    60-90 s: the side that won FB has the wave-control / plate-pop
    initiative; the side that lost FB needs to freeze + recover safely.

    Three branches by killer identity:
      * Active player got FB → "+400g First Blood — DU hast es!"
        (info, gives a momentum nudge to press the lead)
      * An ally got FB → "+400g Team — Wellen mitnehmen, Tempo nutzen"
        (info, signals to follow up the team's advantage)
      * An enemy got FB → "Gegner First Blood — defensiv 90s, Welle freezen"
        (warn, switches the player into safe-play mode)

    Single-fire hysteresis — there's exactly one First Blood per game.
    """
    h = _FIRST_BLOOD_HYSTERESIS
    if h.fired:
        return None

    events = list(getattr(snapshot, "raw_events", []) or [])
    # First ChampionKill chronologically — events typically arrive in
    # order, but sort defensively in case LCDA returns them shuffled.
    fb_event = None
    for evt in sorted(events, key=lambda e: float(e.get("EventTime", 0) or 0)):
        if evt.get("EventName") == "ChampionKill":
            fb_event = evt
            break
    if fb_event is None:
        return None
    killer = str(fb_event.get("KillerName") or "")
    if not killer:
        return None

    h.fired = True

    # Identify killer's team via the snapshot's allies / enemies lists.
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    ally_ids = _team_id_set(allies)
    enemy_ids = _team_id_set(enemies)
    active_summoner = str(getattr(snapshot, "active_summoner", "") or "")

    if killer in ally_ids:
        if killer == active_summoner:
            text = (
                "+400g First Blood — DU hast es! Tempo nutzen: "
                "Welle pushen, Plates ziehen, Snowball starten"
            )
        else:
            text = (
                f"+400g First Blood Team ({killer}) — "
                "Wellen mitnehmen, Plates ziehen, Tempo-Spiel"
            )
        severity, ttl_s, confidence, risk = "info", 60.0, 0.85, "LOW"
    elif killer in enemy_ids:
        text = (
            f"Gegner First Blood ({killer}) — defensiv 90s, "
            "Welle freezen, Vision setzen, Jungler pingen"
        )
        severity, ttl_s, confidence, risk = "warn", 75.0, 0.85, "MEDIUM"
    else:
        # Unknown killer (couldn't match either team) — re-arm so a
        # later, identifiable kill event can fire.
        h.fired = False
        return None

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="first_blood",
        reasons=(
            "First Blood: 300g base + 100g shutdown = 400g pro Killer",
            "Lane-Priority-Shift für ~90s nach FB",
            "Won FB → Plates + Tower-Druck. Lost FB → Freeze + Hilfe.",
        ),
    )


# ─── Teamfight-outcome thresholds ────────────────────────────────────────────
# A "teamfight" here is ≥3 total ChampionKill events in a tight time window.
# This excludes single ganks (1-0) and 2-man skirmishes (2-1, 1-1) — those
# don't carry the same "press the win / recover the loss" stakes.
TEAMFIGHT_WINDOW_S: float = 15.0       # all kills within 15 s = same fight
TEAMFIGHT_MIN_TOTAL_KILLS: int = 3     # at least 3 deaths to count as a fight
TEAMFIGHT_DECISIVE_NET: int = 2        # |ally_kills − ally_deaths| ≥ 2 to fire
TEAMFIGHT_LOPSIDED_NET: int = 3        # ≥ 3 = fully decisive (ace/near-ace)


def _teamfight_outcome_advice(game_time: float, ally_won: bool) -> str:
    """Phase-aware "what to do in the next 30 s" line. Pros adjust by phase:
    early-game wins press for plates, mid-game wins force baron/drake,
    late-game wins force inhibs/elder."""
    if ally_won:
        if game_time < 840.0:        # < 14:00
            return "Plates + Drache forcen, Wave-Pressure, kein Solo-Trade"
        if game_time < 1500.0:       # 14:00 – 25:00
            return "Drache/Baron forcen, Vision in ihrem Jungle, Tower"
        return "Baron / Elder forcen, Inhib pushen, kein Solo-Splitten"
    # Ally lost.
    if game_time < 840.0:
        return "Recall, Wellen freezen, kein Trade vor Reset"
    if game_time < 1500.0:
        return "Defensiv, Drake/Baron abgeben, Pause kaufen"
    return "Defensiv, Inhib protect, alle ablecken vor Engage"


def rule_teamfight_outcome(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface "we just won/lost a teamfight, here's what to do next"
    once per fight (Charter B5 — post-fight conversion / recovery).

    Distinct from existing rules:
      * rule_ace_detected   — fires ONLY on full 5-0 wipes
      * rule_numbers_advantage / _disadvantage — surface CURRENT alive
        counts, can't distinguish "we just won 4-2" from "5v3 because
        of a 2v0 skirmish 30 s ago"

    This rule fires on EVENTS — counts ChampionKill events in the most
    recent 15-second window. ≥3 deaths total, |net| ≥ 2 → decisive
    teamfight, surface phase-aware advice.

    Hysteresis fires once per fight (keyed on the latest event time
    that triggered the rec); subsequent ticks within 15 s of that
    event won't re-fire even though the events stay in the window.
    """
    events = list(getattr(snapshot, "raw_events", []) or [])
    if not events:
        return None

    # Identify ally vs enemy via summoner_name + champion_name id sets.
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    ally_ids = _team_id_set(allies)
    enemy_ids = _team_id_set(enemies)
    if not ally_ids or not enemy_ids:
        return None

    # Find the latest ChampionKill (anchor of the fight window).
    kill_events = [e for e in events if e.get("EventName") == "ChampionKill"]
    if not kill_events:
        return None
    kill_events.sort(key=lambda e: float(e.get("EventTime", 0) or 0))
    latest_t = float(kill_events[-1].get("EventTime", 0) or 0)
    window_start = latest_t - TEAMFIGHT_WINDOW_S
    fight_kills = [
        e for e in kill_events
        if float(e.get("EventTime", 0) or 0) >= window_start
    ]
    if len(fight_kills) < TEAMFIGHT_MIN_TOTAL_KILLS:
        return None

    # Score the fight from the active player's team perspective.
    ally_kills = 0
    enemy_kills = 0
    for evt in fight_kills:
        killer = evt.get("KillerName") or ""
        if killer in ally_ids:
            ally_kills += 1
        elif killer in enemy_ids:
            enemy_kills += 1
    net = ally_kills - enemy_kills

    if abs(net) < TEAMFIGHT_DECISIVE_NET:
        return None  # trade fight, no decisive outcome to coach on

    # Hysteresis — don't re-fire the same fight on subsequent ticks.
    h = _TEAMFIGHT_OUTCOME_HYSTERESIS
    if abs(latest_t - h.last_fired_event_time) < TEAMFIGHT_WINDOW_S:
        return None
    h.last_fired_event_time = latest_t

    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    ally_won = net > 0
    advice = _teamfight_outcome_advice(game_time, ally_won)

    if ally_won and net >= TEAMFIGHT_LOPSIDED_NET:
        text = f"Teamfight {ally_kills}-{enemy_kills} GEWONNEN — JETZT {advice}"
        severity, ttl_s, confidence, risk = "alert", 30.0, 0.92, "LOW"
        kind = "teamfight_won_big"
    elif ally_won:
        text = f"Teamfight {ally_kills}-{enemy_kills} gewonnen — Tempo: {advice}"
        severity, ttl_s, confidence, risk = "info", 25.0, 0.85, "LOW"
        kind = "teamfight_won"
    elif net <= -TEAMFIGHT_LOPSIDED_NET:
        text = f"Teamfight {ally_kills}-{enemy_kills} VERLOREN — KEIN Engage. {advice}"
        severity, ttl_s, confidence, risk = "alert", 35.0, 0.92, "HIGH"
        kind = "teamfight_lost_big"
    else:
        text = f"Teamfight {ally_kills}-{enemy_kills} verloren — defensiv. {advice}"
        severity, ttl_s, confidence, risk = "warn", 30.0, 0.85, "HIGH"
        kind = "teamfight_lost"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo" if ally_won else "safety",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind=kind,
        reasons=(
            f"Letzte 15s: {ally_kills} Team-Kills, {enemy_kills} Tode (Net {net:+d})",
            "Pro-Maxime: Win → Konvertiere SOFORT. Loss → Reset BEVOR Re-Engage.",
            "Solo-Queue-Throw #1: nach verlorenem Fight weiter engagen",
        ),
    )


def _streak_to_tier(streak: int) -> int:
    """Map a kill-streak count to the bounty tier it corresponds to (or 0)."""
    if streak >= BOUNTY_TIER_GODLIKE_S:
        return BOUNTY_TIER_GODLIKE_S
    if streak >= BOUNTY_TIER_WARN_S:
        return BOUNTY_TIER_WARN_S
    if streak >= BOUNTY_TIER_INFO_S:
        return BOUNTY_TIER_INFO_S
    return 0


def _shutdown_phase_advice(game_time: float, tier: int) -> str:
    """Phase-aware "what to convert the shutdown into" line. Higher-bounty
    shutdowns earn bigger plays — at +500g you're force-resetting the game,
    not popping a single plate."""
    big = tier >= BOUNTY_TIER_GODLIKE_S
    if game_time < 840.0:        # < 14:00
        return "Plates + Drache forcen, Wave-Pressure" if not big else \
               "Plates + Drache + Tower hard pushen — Tempo-Reset"
    if game_time < 1500.0:       # 14:00 – 25:00
        return "Drake / Tower forcen, Vision in ihrem Jungle" if not big else \
               "Baron oder Inhib forcen — Game-Reset-Window"
    return "Inhib oder Elder forcen, Vision-Sweep" if not big else \
           "Baron + Inhib JETZT — Win-Condition"


def rule_shutdown_taken(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fire when a bountied enemy just died — convert the +150/+300/+500g
    shutdown gold + their long respawn timer into objectives (Charter B5).

    The complement to rule_enemy_bounty:
      * rule_enemy_bounty   — "Jinx is on UNSTOPPABLE — pick on her"
      * rule_shutdown_taken — "Jinx down with shutdown — push for Drake NOW"

    Solo queue celebrates the kill but doesn't convert the next 30-60 s
    of respawn-timer + bonus gold into map state. Pros do exactly this:
    ping the team to rotate to whichever objective is closest to up.

    Mechanics
    ---------
    Tracks each enemy's highest tier reached *while alive* —
    ``_kill_streak`` resets to 0 the moment the death event lands in
    raw_events, so by the time we see ``is_alive=False`` the streak
    info is already gone. Capturing it pre-death and reading it on the
    death tick recovers the conversion signal.

    ``fired_for_death`` keys on the enemy's death-counter so the rec
    fires once per death-instance, not every tick they're on respawn.

    Three tiers, by pre-death streak:
    * tier 3-4 → info — "Shutdown auf X (+150g) — Tempo: …"
    * tier 5-6 → warn — "Shutdown auf X (+300g) — Drake/Baron forcen"
    * tier 7+  → alert — "Shutdown X (+500g) — Game-Reset, Baron + Inhib JETZT"
    """
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not enemies:
        return None
    events = list(getattr(snapshot, "raw_events", []) or [])
    h = _SHUTDOWN_TAKEN_HYSTERESIS

    # Pass 1 — refresh per-enemy "tier while alive" so we have it
    # captured for the moment they die.
    for e in enemies:
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        if not getattr(e, "is_alive", True):
            continue
        current_tier = _streak_to_tier(_kill_streak(e, events))
        # Always store the highest-yet seen tier (don't drop it back when
        # they get a kill that doesn't escalate the tier).
        h.last_alive_tier[name] = max(
            h.last_alive_tier.get(name, 0), current_tier,
        )

    # Pass 2 — pick the most-actionable shutdown to announce: highest
    # pre-death tier among enemies who just died and haven't been
    # announced yet for this death-instance.
    best: tuple[int, int, str] | None = None  # (tier, deaths, name)
    for e in enemies:
        if getattr(e, "is_alive", True):
            continue
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        deaths = int(getattr(e, "deaths", 0) or 0)
        if deaths <= h.fired_for_death.get(name, -1):
            continue  # already announced for this death
        pre_death_tier = h.last_alive_tier.get(name, 0)
        if pre_death_tier < BOUNTY_TIER_INFO_S:
            continue  # they didn't have a streak — no shutdown to convert
        candidate = (pre_death_tier, deaths, name)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, deaths, name = best
    h.fired_for_death[name] = deaths
    # Reset their alive-tier so a fresh life with a fresh streak earns a
    # new shutdown announcement when it ends.
    h.last_alive_tier[name] = 0

    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    advice = _shutdown_phase_advice(game_time, tier)

    if tier >= BOUNTY_TIER_GODLIKE_S:
        text = (
            f"SHUTDOWN auf {name} (+500g) — GAME-RESET-Fenster. "
            f"{advice}"
        )
        severity, ttl_s, confidence, risk = "alert", 35.0, 0.92, "LOW"
    elif tier >= BOUNTY_TIER_WARN_S:
        text = (
            f"Shutdown auf {name} (+300g) — Konvertieren: {advice}"
        )
        severity, ttl_s, confidence, risk = "warn", 30.0, 0.88, "LOW"
    else:
        text = (
            f"Shutdown auf {name} (+150g) — Tempo nutzen: {advice}"
        )
        severity, ttl_s, confidence, risk = "info", 25.0, 0.80, "LOW"

    return Recommendation(
        text=text,
        severity=severity,
        category="tempo",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="shutdown_taken",
        reasons=(
            f"{name} starb auf Streak (Tier {tier} — Bounty kassiert)",
            "Pro-Maxime: Shutdown-Gold IMMER in Map-State konvertieren",
            "Solo-Queue-Throw: Kill bestätigen + nichts pushen = Bounty verschwendet",
        ),
    )


# ─── Objective-taken thresholds ──────────────────────────────────────────────
# After taking a major objective, the buff window is finite. Pros immediately
# convert into map state — most importantly into Inhib pressure for Baron and
# tower/drake stacks for the others. Solo queue often drops the buff.
OBJECTIVE_TAKEN_RECENT_S: float = 20.0   # how recently the kill must have happened


_OBJECTIVE_LABEL: dict[str, str] = {
    "BaronKill":   "Baron",
    "DragonKill":  "Drache",
    "HeraldKill":  "Herald",
}


def _objective_taken_advice(
    event_name: str, drake_detail: str, ally_drake_count: int, game_time: float,
) -> tuple[str, str, str, float, float, str]:
    """Return ``(text_suffix, severity, risk, ttl_s, confidence, kind)`` for
    the just-taken objective, factoring in the dragon-soul / elder edge cases.

    Tier scaling:
      * Baron      → alert, push all 3 lanes, force inhib
      * Elder      → alert, 3-min execute, force inhib JETZT
      * Soul drake → alert, permanent buff, force baron / inhib
      * Regular dr → info,  next drake setup, vision
      * Herald     → info,  eye-of-herald → tower
    """
    if event_name == "BaronKill":
        return (
            "BARON taken — alle 3 Lanes pushen, Inhib forcen, Vision-Sweep",
            "alert", "LOW", 30.0, 0.92, "objective_taken_baron",
        )
    if event_name == "DragonKill":
        # Elder Dragon: only spawns after a soul has been claimed.
        if (drake_detail or "").lower() == "elder":
            return (
                "ELDER taken — 3-min Execute aktiv, INHIB JETZT, kein Wait",
                "alert", "LOW", 30.0, 0.92, "objective_taken_elder",
            )
        # Soul drake: 4th drake claim seals the soul buff (permanent for this game).
        if ally_drake_count >= 4:
            return (
                "SOUL taken — permanenter Buff. Baron oder Inhib forcen.",
                "alert", "LOW", 30.0, 0.90, "objective_taken_soul",
            )
        # Regular drake.
        return (
            "Drache taken — nächste Drake setup, Vision pflanzen",
            "info", "LOW", 25.0, 0.80, "objective_taken_drake",
        )
    # Herald.
    if game_time < 840.0:
        advice = "Eye-of-Herald nutzen, Top oder Mid Tower aufknacken"
    else:
        advice = "Herald-Charge nutzen, Plates oder Tower"
    return (advice, "info", "LOW", 25.0, 0.80, "objective_taken_herald")


def rule_objective_taken_by_ally(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fire when the ally team just killed Baron / Dragon / Herald. The
    *post-kill* conversion call (Charter B5).

    Distinct from existing rules:
      * rule_dragon_window / rule_baron_window — fire while objective is UP
      * rule_baron_buff_expiring — fires near the END of the buff
      * rule_dragon_soul_pressure — fires PRE-soul (3 stacks)

    This rule is the missing AT-THE-MOMENT-OF-KILL signal: "you just took
    it, here's the immediate next play". Solo queue routinely takes Baron
    and then rotates BACK to drake, dropping the inhib pressure window.

    Picks the most recent ally-team objective kill within
    OBJECTIVE_TAKEN_RECENT_S seconds of game_time, fires once per
    EventTime so the cumulative event log can't re-fire the same kill.
    """
    events = list(getattr(snapshot, "raw_events", []) or [])
    if not events:
        return None
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    allies = list(getattr(snapshot, "allies", []) or [])
    ally_ids = _team_id_set(allies)
    if not ally_ids:
        return None
    h = _OBJECTIVE_TAKEN_HYSTERESIS

    # Find the most recent ally-team objective kill in the recent window.
    candidate: dict | None = None
    candidate_t = -1.0
    for evt in events:
        name = evt.get("EventName") or ""
        if name not in _OBJECTIVE_LABEL:
            continue
        killer = str(evt.get("KillerName") or "")
        if killer not in ally_ids:
            continue
        t = float(evt.get("EventTime", 0) or 0)
        if game_time - t > OBJECTIVE_TAKEN_RECENT_S:
            continue
        if t > candidate_t:
            candidate = evt
            candidate_t = t

    if candidate is None:
        return None
    name = candidate.get("EventName", "") or ""
    key = (name, float(candidate.get("EventTime", 0) or 0))
    if key in h.fired_event_times:
        return None
    h.fired_event_times.add(key)

    # Pull the dragon stack count for soul / elder detection.
    ally_aggregate = getattr(snapshot, "ally_aggregate", None)
    ally_drakes = int(getattr(ally_aggregate, "dragons", 0) or 0)
    drake_detail = str(candidate.get("DragonType") or "")

    advice_text, severity, risk, ttl_s, confidence, kind = _objective_taken_advice(
        name, drake_detail, ally_drakes, game_time,
    )
    label = _OBJECTIVE_LABEL.get(name, name)

    return Recommendation(
        text=f"{label} GETÖTET — {advice_text}",
        severity=severity,
        category="objective",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind=kind,
        reasons=(
            f"{label}-Kill von Team registriert (T={int(candidate_t)}s)",
            "Pro-Maxime: Buffs SOFORT in Map-State konvertieren",
            "Solo-Queue-Throw: Baron töten + zurück zur Drake = Buff verschwendet",
        ),
    )


# ─── Objective-bounty thresholds ─────────────────────────────────────────────
# Riot's catch-up bounty system kicks in when one team is significantly ahead.
# Trailing team gets bonus gold from objectives + kills; leading team's
# deaths spawn shutdowns. The exact formula scales with game-time, but a
# 4-5k items_value differential is a reliable proxy for "bounties are
# active" in patches 14.x+.
OBJECTIVE_BOUNTY_DIFF_THRESHOLD: float = 4500.0   # |items_value diff| ≥ this
OBJECTIVE_BOUNTY_REARM_THRESHOLD: float = 3000.0  # diff drops below this → re-arm
# Bounty mechanic only matters in mid-game. Before 8 min there's not enough
# differential; past 35 min the pace is too late-game for bounties to swing.
OBJECTIVE_BOUNTY_PHASE_START_S: float = 480.0    # 8:00
OBJECTIVE_BOUNTY_PHASE_END_S: float = 2100.0     # 35:00


def rule_objective_bounty_active(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Two-sided "Riot bounty system is currently affecting the game" notice.

    Pros explicitly think about catch-up bounties:
      * Behind 4k+: "objectives give bonus gold, force them — comeback IS
        mathematically supported". Solo-queue's #1 mid-game FF mistake is
        not knowing the bounty math.
      * Ahead 4k+: "your deaths are worth shutdown gold to them — extra
        careful, no greedy 1v1s". Solo-queue gets cocky when ahead and
        trades the lead back through unaware deaths.

    Distinct from existing rules:
      * rule_far_behind_safe — generic "play safe when behind"
      * rule_gold_lead_push — generic "press the lead"
    Both are gold-diff threshold rules but reference *general* play
    advice. This rule specifically mentions the bounty mechanic so the
    user understands WHY the action is right (comeback math; defensive
    valuation of own life).

    Single-fire per state-change (entering the threshold band fires once;
    leaving it via the rearm threshold re-arms for the next swing).
    """
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    if not (OBJECTIVE_BOUNTY_PHASE_START_S <= game_time <= OBJECTIVE_BOUNTY_PHASE_END_S):
        return None

    diff = _team_gold_diff(snapshot)
    h = _OBJECTIVE_BOUNTY_HYSTERESIS

    # Re-arm when the gap closes below the rearm threshold.
    abs_diff = abs(diff)
    if abs_diff < OBJECTIVE_BOUNTY_REARM_THRESHOLD:
        h.fired_behind = False
        h.fired_ahead = False
        return None

    if abs_diff < OBJECTIVE_BOUNTY_DIFF_THRESHOLD:
        return None

    # Behind branch.
    if diff < 0 and not h.fired_behind:
        h.fired_behind = True
        gap_k = round(abs_diff / 1000.0, 1)
        return Recommendation(
            text=(
                f"-{gap_k}k Gold — Objective-Bounties aktiv. Drake / Baron / "
                "Tower geben Bonus-Gold beim Take. Force-Objectives lohnen."
            ),
            severity="info",
            category="tempo",
            confidence=0.78,
            risk="MEDIUM",
            ttl_s=45.0,
            kind="objective_bounty_behind",
            reasons=(
                f"Team-Gold-Diff: {int(diff)}",
                "Riot Catch-Up-System: Bonus-Gold auf Objectives + Kills",
                "Comeback-Math: 1 Baron-Bounty + Inhib = ~5k Swing",
            ),
        )
    # Ahead branch.
    if diff > 0 and not h.fired_ahead:
        h.fired_ahead = True
        gap_k = round(abs_diff / 1000.0, 1)
        return Recommendation(
            text=(
                f"+{gap_k}k Gold — Vorsicht: Objective-Bounties auf eurer "
                "Seite. Tod kostet Shutdown-Gold + Spike. Kein Greed."
            ),
            severity="info",
            category="safety",
            confidence=0.78,
            risk="MEDIUM",
            ttl_s=45.0,
            kind="objective_bounty_ahead",
            reasons=(
                f"Team-Gold-Diff: +{int(diff)}",
                "Bounty-System: jeder Tod gibt Gegner +Y g extra",
                "Pro-Maxime: Lead halten via Vision + Map-Control, nicht 1v1",
            ),
        )

    return None


def rule_tilt_detection(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Surface the active player's death pattern as a coaching call.

    Five tiers:
      * caution  — first lane death; freeze + ping
      * tilt     — 2 deaths in 90s; the classic tilt window
      * re_engage — 2 deaths in 60s; "1-and-done", do nothing for 30s
      * spiral   — 3 deaths in 180s OR 2 in 60s; hard reset

    Modifiers append to the rec text:
      * bounty_lost: "+ Bounty (3+ Streak) verloren — ~600g extra für Gegner"
      * solo_death:  "+ Alleine gestorben — keine Side-Lane mehr"

    The phase-aware advice line replaces the generic "play safe" because
    "play safe" means different things in lane vs mid-game vs late-game.
    Solo-queue users who *know* what playing safe means don't need
    coaching; the rest get concrete actions.
    """
    state = getattr(snapshot, "tilt_state", None)
    if state is None or getattr(state, "severity", "ok") == "ok":
        return None

    severity_tier = state.severity
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)
    phase_advice = _tilt_phase_advice(game_time)

    if severity_tier == "spiral":
        text = f"DEATH SPIRAL — {state.deaths_recent_180s} Tode in 3min. STOP. 60s NICHT zeigen — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 90.0, 0.95, "HIGH"
    elif severity_tier == "re_engage":
        text = f"1-AND-DONE — 2 Tode in 60s. Direkt nach Respawn wieder rein = Disaster. 30s warten — {phase_advice}"
        severity, ttl_s, confidence, risk = "alert", 75.0, 0.90, "HIGH"
    elif severity_tier == "tilt":
        text = f"Tilt-Fenster — 2 Tode in 90s. Länger basen, 2 Components kaufen, {phase_advice}"
        severity, ttl_s, confidence, risk = "warn", 60.0, 0.85, "HIGH"
    else:  # caution — single lane death
        text = f"Erster Tod — {phase_advice}, kein Comeback-1v1 versuchen"
        severity, ttl_s, confidence, risk = "info", 30.0, 0.65, "MEDIUM"

    # Modifier suffixes — appended only when the modifier is true.
    # Reasons get the plain facts; text gets the actionable suffix.
    modifiers: list[str] = []
    reasons: list[str] = [
        f"Tode total: {state.deaths_total}",
        f"Tode in 90s: {state.deaths_recent_90s}",
    ]
    if state.bounty_lost:
        modifiers.append("+ Bounty (3+ Streak) verloren — ~600g extra Gegner")
        reasons.append("Bounty: 3+ unanswered kills vor letztem Tod verloren")
    if state.solo_death:
        modifiers.append("+ Alleine gestorben — keine Side-Lane mehr")
        reasons.append("Letzter Tod ohne Ally-Beteiligung — Positionierungsfehler")
    if modifiers:
        text = text + "  " + "  ".join(modifiers)

    return Recommendation(
        text=text,
        severity=severity,
        category="safety",
        confidence=confidence,
        risk=risk,
        ttl_s=ttl_s,
        kind="tilt",
        reasons=tuple(reasons),
    )


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


# ─── Objective setup-window thresholds ────────────────────────────────────────
# Pre-spawn coaching for drake / baron / herald / void grubs. The existing
# rule_dragon_window / rule_baron_window fire WHEN the objective is up — this
# rule fires the 30-90s window *before* spawn, when waves still have time to
# crash and rotation is the play.
#
# Why 30-90s:
#   90s — earliest you'd start prepping. Beyond this is too far out to
#         influence the wave that'll crash at spawn.
#   30s — last possible push. Below 30s the wave is already locked in;
#         you're either there or you're not, and the existing
#         drake/baron-window rules cover the actual fight.
SETUP_WINDOW_MIN_S: float = 30.0
SETUP_WINDOW_MAX_S: float = 90.0

# Priority ordering when multiple objectives sit in the setup window
# at the same time. Baron > Dragon > Herald > Void Grubs reflects what
# pros actually contest first when forced to pick.
_OBJECTIVE_PRIORITY: dict[str, int] = {
    "Baron":     4,
    "Dragon":    3,
    "Herald":    2,
    "VoidGrubs": 1,
}

# Per-objective × position setup advice. Six lane-specific strings per
# objective is overkill for V1; instead we tag advice by "near vs far" —
# whether the active player's lane is on the same side as the pit.
# Drake = bot side; Baron / Herald / Void Grubs = top side.
_OBJECTIVE_SIDE: dict[str, str] = {
    "Dragon":    "BOTTOM",
    "Baron":     "TOP",
    "Herald":    "TOP",
    "VoidGrubs": "TOP",
}


def _objective_setup_advice(
    objective: str, active_position: str, game_time: float,
) -> str:
    """Pro-tuned setup line per objective × proximity to pit.

    "Near pit": active player's lane is on the same map side as the
    objective. They prep vision + push their wave in place.
    "Far pit": active player needs to rotate. Push hard, get TP up,
    or coordinate a wave swap with team.
    """
    pit_side = _OBJECTIVE_SIDE.get(objective, "")
    near_pit = active_position == pit_side or (
        # Bot 2-vs-2: support lane is bottom too.
        active_position == "UTILITY" and pit_side == "BOTTOM"
    )

    if objective == "Dragon":
        if near_pit:
            return "Welle pushen, Pixel-Buschwerk warden, Pit-Control"
        if active_position == "MIDDLE":
            return "Mid-Welle shoven, dann rotieren, Vision setzen"
        if active_position == "JUNGLE":
            return "Pit + Drachen-Buschwerk warden, Smite ready"
        # TOP — far from pit
        return "Welle hard pushen, TP bereithalten, dann rotieren"

    if objective == "Baron":
        if near_pit:
            return "Pit-Vision setzen, Welle pushen, Tank-Position"
        if active_position == "MIDDLE":
            return "Mid pushen + River-Vision, dann zum Pit"
        if active_position == "JUNGLE":
            return "Pit + River warden, Smite ready, Vision-Sweeper"
        # BOTTOM / UTILITY — far from baron
        return "Bot-Welle resetten, dann zum Pit gruppieren"

    if objective == "Herald":
        if near_pit:
            return "Welle pushen, River-Buschwerk warden, Pit-Control"
        if active_position == "MIDDLE":
            return "Mid pushen, dann zum Top-River rotieren"
        if active_position == "JUNGLE":
            return "Pit + Top-River warden, Smite ready"
        # BOTTOM / UTILITY — far from herald
        return "Welle pushen, Drache-Setup oder Top-Side mitspielen"

    if objective == "VoidGrubs":
        if near_pit:
            return "Welle pushen, Pit-Vision, Top-Side gruppieren"
        if active_position == "JUNGLE":
            return "Pit warden + Smite ready, Top mitnehmen"
        return "Welle pushen, Vision-Pressure, Top-Side mitspielen"

    return "Welle pushen + Vision setzen"


def rule_objective_setup_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Fire 30–90 s before drake / baron / herald / void grubs spawn so the
    player has time to crash a wave + set vision + rotate (Charter B3).

    Picks the highest-priority objective in the setup window; one rec per
    tick at most. Existing ``rule_dragon_window`` / ``rule_baron_window``
    take over once the objective is actually up.

    Position-aware advice differentiates "near-pit" (push in place + vision)
    from "far-pit" (push hard + rotate / TP). JUNGLE always gets vision +
    smite-ready advice since they're the one expected to reach the pit first.
    """
    objectives = list(getattr(snapshot, "objectives", []) or [])
    if not objectives:
        return None
    game_time = float(getattr(snapshot, "game_time", 0.0) or 0.0)

    # Find the most-actionable objective: highest priority among any
    # whose remaining-time falls inside the setup window.
    best: tuple[int, float, str] | None = None  # (priority, remaining, name)
    for obj in objectives:
        name = str(getattr(obj, "name", "") or "")
        remaining = obj.remaining(game_time) if hasattr(obj, "remaining") else None
        if remaining is None:
            continue
        if not (SETUP_WINDOW_MIN_S <= remaining <= SETUP_WINDOW_MAX_S):
            continue
        prio = _OBJECTIVE_PRIORITY.get(name, 0)
        if prio == 0:
            continue
        if best is None or prio > best[0]:
            best = (prio, remaining, name)

    if best is None:
        return None
    _, remaining, name = best

    # Active-player position drives the advice variant.
    active_player = _active_player(snapshot)
    active_position = str(getattr(active_player, "position", "") or "")
    advice = _objective_setup_advice(name, active_position, game_time)

    label = {
        "Dragon": "Drache",
        "Baron": "Baron",
        "Herald": "Herald",
        "VoidGrubs": "Void Grubs",
    }.get(name, name)

    return Recommendation(
        text=f"{label} in {int(remaining)}s — {advice}",
        severity="info",
        category="objective",
        confidence=0.80,
        risk="LOW",
        ttl_s=20.0,
        kind="objective_setup",
        reasons=(
            f"{label} spawnt in {int(remaining)}s",
            "Setup-Fenster: Welle crashen lassen + Vision = on-time bei Spawn",
        ),
    )


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


