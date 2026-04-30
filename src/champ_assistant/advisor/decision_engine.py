"""Decision engine — turns raw LCDA state into actionable recommendations.

Strategy B1 — first foundation of the smartest pillar. Pure functions
over an LCDA snapshot; no Qt, no I/O, no asyncio. Each rule encodes
one heuristic the assistant would tell a teammate at that game state.

Honest scope (V1)
=================
This is a curated rule set, NOT an ML model. Five rules cover the
high-leverage patterns where a quick text nudge is genuinely useful:

  1. Drake-priority — drake up soon AND team has resources to take it
  2. Drake-give-up  — drake up AND team is behind, contest is bad ROI
  3. Gold-lead push — meaningful lead, time to convert into vision/objs
  4. Far-behind safe — significant deficit, correct play is wave clear
  5. Level-deficit  — average level gap large enough to lose any fight

Rules return ``Recommendation`` objects with severity + category +
text. The caller (UI panel, future B5 recommendation surface) picks
which to display.

What this is NOT
----------------
* No enemy-position detection (Vanguard-incompatible).
* No matchup-specific advice (would need curated dataset).
* No teamfight-readiness model — that requires ult availability data
  we don't have.
* Not an oracle — heuristics are approximations, the user remains in
  control. Every recommendation is a hint, never a command.

Adding rules
============
Each rule is a pure function ``(snapshot) -> Recommendation | None``.
Register it in ``ALL_RULES`` to plug it into ``evaluate``. Rules may
read any LCDA-derived field; they MUST defensively handle missing
data (most fields can be None / default during the first few seconds
of a game).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..lcda.objectives import ObjectiveTimer
    from ..lcda.source import LcdaSnapshot

# Thresholds — tunables. Pulled out so future re-tuning is one file.
DRAKE_PRIORITY_WINDOW_S = 30.0  # drake spawning within this is "soon"
BARON_PRIORITY_WINDOW_S = 45.0  # baron is more impactful → wider lead-up window
HERALD_LATE_GAME_S = 14 * 60.0  # herald despawns ~14:00; rule silent after
GOLD_LEAD_THRESHOLD = 3000      # absolute item-value diff that counts as "ahead"
GOLD_DEFICIT_THRESHOLD = 5000   # behind by this → play safe, don't force
LEVEL_GAP_THRESHOLD = 1.5       # avg-level diff that makes fighting bad
KILL_LEAD_THRESHOLD = 5         # kills ahead → real momentum, press it
KILL_DEFICIT_THRESHOLD = 7      # kills behind → real deficit, bunker
LATE_GAME_S = 30 * 60.0         # past 30:00 every fight is the last fight


@dataclass(frozen=True)
class Recommendation:
    """One actionable hint. Severity sorts these in the UI; category
    groups them so the user can see at a glance whether it's an
    attacking play, a safety call, or an objective decision.

    The confidence / risk / ttl_s fields were added in the v2 spec
    pass — they describe HOW SURE we are about the call, what the
    downside looks like, and how long the call stays valid. All
    three default to conservative values so legacy rules that didn't
    set them still produce sensible output."""
    text: str           # human-readable, German, short ("Drache forcen")
    severity: str       # "info" | "warn" | "alert"
    category: str       # "objective" | "tempo" | "safety" | "lane"
    # v2 additions — confidence band 0..1 (UI renders as a bar / glow),
    # risk band drives danger-coloring of the action, ttl_s tells the
    # UI how long this hint stays relevant before it should fade.
    confidence: float = 0.7    # default = "rule fired, moderate confidence"
    risk: str = "MEDIUM"       # "LOW" | "MEDIUM" | "HIGH"
    ttl_s: float = 15.0        # seconds before the hint becomes stale
    # Bulletpoints the InsightPanel renders when the user expands a
    # recommendation. Each entry is a short factual statement of WHY
    # the rule fired ("Drache in 25s", "Team-Gold-Diff +2400"). Empty
    # tuple is the safe default — legacy rules without explicit
    # reasons just don't expand.
    reasons: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _team_gold_diff(snapshot: "LcdaSnapshot") -> int:
    """Allies items_value minus enemies items_value. Positive when
    we're ahead. None aggregates collapse to 0 — be defensive."""
    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    a = getattr(ally, "items_value", 0) if ally is not None else 0
    e = getattr(enemy, "items_value", 0) if enemy is not None else 0
    if not isinstance(a, (int, float)) or not isinstance(e, (int, float)):
        return 0
    return int(a) - int(e)


def _avg_level_diff(snapshot: "LcdaSnapshot") -> float:
    """Average ally level minus average enemy level. Positive when
    we're ahead. Empty teams return 0."""
    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    if not allies or not enemies:
        return 0.0
    a = sum(getattr(p, "level", 0) for p in allies) / len(allies)
    e = sum(getattr(p, "level", 0) for p in enemies) / len(enemies)
    return a - e


def fight_score(snapshot: "LcdaSnapshot | None") -> float:
    """Layer-2 scoring (v2 spec): weighted sum of advantage signals →
    a single 'how good is fighting right now' number in [-1.0..+1.0].

    Positive = we win this fight, negative = we lose. The mapping is
    intentionally calibrated against the existing thresholds:

      * gold_diff: ±5000 saturates one full point (≈ kill-spree gap)
      * level_diff: ±2.0 levels saturates (1 level ≈ 0.5 point)
      * kill_diff: ±10 saturates (large team-snowball)

    Pure function — no side effects. Used by future Layer-3 prediction
    helpers + as a confidence input for individual rules.
    """
    if snapshot is None:
        return 0.0
    gold = _team_gold_diff(snapshot)
    levels = _avg_level_diff(snapshot)
    kills = _team_kill_diff(snapshot)
    score = (
        max(-1.0, min(1.0, gold / 5000.0)) * 0.45
        + max(-1.0, min(1.0, levels / 2.0)) * 0.30
        + max(-1.0, min(1.0, kills / 10.0)) * 0.25
    )
    return max(-1.0, min(1.0, score))


def win_probability(snapshot: "LcdaSnapshot | None") -> float:
    """Layer-3 prediction (v2 spec): logistic-shape mapping of
    fight_score into a [0..1] win-probability estimate.

    Heuristic, not a trained model — fight_score is already bounded
    [-1..1] so the logistic just smooths it into a probability that
    the UI can render as a bar / percentage. Future swap-in of a real
    regression model is a single function-body change.
    """
    s = fight_score(snapshot)
    # Logistic with steepness 3 — roughly 0.95 at +1, 0.05 at -1.
    import math
    return 1.0 / (1.0 + math.exp(-3.0 * s))


def _objective_remaining(
    snapshot: "LcdaSnapshot", name: str,
) -> float | None:
    """Seconds until ``name`` respawns. None if not killed yet or not
    available in the snapshot."""
    for obj in getattr(snapshot, "objectives", []) or []:
        if getattr(obj, "name", "") == name:
            try:
                return obj.remaining(getattr(snapshot, "game_time", 0.0))
            except Exception:  # noqa: BLE001
                return None
    return None


def _alive_count(players: list, default_to_full_team: bool = True) -> int:
    """Count players currently on the map (respawn_timer == 0).
    Falls back to ``len(players)`` when respawn data isn't carried
    (older LCDA payloads / replayed fixtures) — alternative is to
    silently report everyone as dead, which would spam fight-avoidance
    recommendations during the early-game window."""
    if not players:
        return 0
    has_respawn = any(
        getattr(p, "respawn_timer", None) is not None for p in players
    )
    if not has_respawn and default_to_full_team:
        return len(players)
    return sum(1 for p in players if getattr(p, "is_alive", True))


def _team_kill_diff(snapshot: "LcdaSnapshot") -> int:
    """Allies' total kills minus enemies'. Positive when we're snowballing.
    Falls back to summing per-player kills when team aggregates are
    missing."""
    ally = getattr(snapshot, "ally_aggregate", None)
    enemy = getattr(snapshot, "enemy_aggregate", None)
    a = getattr(ally, "kills", None) if ally is not None else None
    e = getattr(enemy, "kills", None) if enemy is not None else None
    if a is None:
        a = sum(
            getattr(p, "kills", 0)
            for p in (getattr(snapshot, "allies", []) or [])
        )
    if e is None:
        e = sum(
            getattr(p, "kills", 0)
            for p in (getattr(snapshot, "enemies", []) or [])
        )
    if not isinstance(a, (int, float)) or not isinstance(e, (int, float)):
        return 0
    return int(a) - int(e)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

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
        text=f"+{gold} Gold — Vision pushen, Wellen kontrollieren, "
             f"nächstes Objective vorbereiten",
        severity="info",
        category="tempo",
        confidence=0.75,
        risk="LOW",
        ttl_s=20.0,
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
        text=f"+{diff} Kills — Vision deep pushen, dive-Comp hinten "
             f"einrichten",
        severity="info",
        category="tempo",
        confidence=0.78,
        risk="LOW",
        ttl_s=25.0,
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
    allies_alive = _alive_count(getattr(snapshot, "allies", []) or [])
    enemies_alive = _alive_count(getattr(snapshot, "enemies", []) or [])
    if allies_alive >= enemies_alive:
        return None
    deficit = enemies_alive - allies_alive
    if deficit <= 0:
        return None
    return Recommendation(
        text=f"{deficit} Mate(s) tot — KEINE Fights, "
             f"defensiv positionieren bis Respawn",
        severity="alert",
        category="safety",
        confidence=0.92,
        risk="HIGH",
        ttl_s=8.0,
        reasons=(
            f"Allies alive: {allies_alive}/5",
            f"Enemies alive: {enemies_alive}/5",
            "Numbers-Disadvantage = Death-Risk × 5",
        ),
    )


def rule_numbers_advantage(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Enemies dead → push the temporary 5v4 / 5v3. The window is
    short (single death = ~30s), so the rec ttl matches that."""
    allies_alive = _alive_count(getattr(snapshot, "allies", []) or [])
    enemies_alive = _alive_count(getattr(snapshot, "enemies", []) or [])
    if enemies_alive >= allies_alive:
        return None
    advantage = allies_alive - enemies_alive
    if advantage <= 0:
        return None
    return Recommendation(
        text=f"{advantage} Gegner tot — Pressure machen, "
             f"Objective forcen oder Wave-Crash",
        severity="alert",
        category="tempo",
        confidence=0.90,
        risk="LOW",
        ttl_s=12.0,
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


# Rule registry — extend by appending a function. Order doesn't affect
# ``evaluate``'s output (caller sorts by severity).
ALL_RULES: tuple[Callable[["LcdaSnapshot"], Recommendation | None], ...] = (
    # Numbers-asymmetry rules first — if a teammate is dead we want
    # the SAFETY call to dominate over any objective-priority rule.
    rule_numbers_disadvantage,
    rule_numbers_advantage,
    rule_drake_priority,
    rule_drake_give_up,
    rule_baron_priority,
    rule_baron_give_up,
    rule_herald_priority,
    rule_gold_lead_push,
    rule_far_behind_safe,
    rule_level_deficit,
    rule_kill_lead_snowball,
    rule_kill_deficit_defensive,
    rule_late_game_group,
)


_SEVERITY_RANK = {"alert": 0, "warn": 1, "info": 2}


def evaluate(
    snapshot: "LcdaSnapshot | None",
    *,
    rules: tuple = ALL_RULES,
) -> list[Recommendation]:
    """Run every rule against ``snapshot`` and return the non-None
    results sorted by severity (alerts first). Pure function — safe
    to call on the LCDA-snapshot tick without any state.

    None snapshot → empty list (pre-game window). Rules that raise
    are silently skipped — a buggy rule must not break the engine.
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
    out.sort(key=lambda r: _SEVERITY_RANK.get(r.severity, 99))
    return out
