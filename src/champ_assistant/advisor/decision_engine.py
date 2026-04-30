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
GOLD_LEAD_THRESHOLD = 3000      # absolute item-value diff that counts as "ahead"
GOLD_DEFICIT_THRESHOLD = 5000   # behind by this → play safe, don't force
LEVEL_GAP_THRESHOLD = 1.5       # avg-level diff that makes fighting bad


@dataclass(frozen=True)
class Recommendation:
    """One actionable hint. Severity sorts these in the UI; category
    groups them so the user can see at a glance whether it's an
    attacking play, a safety call, or an objective decision."""
    text: str           # human-readable, German, short ("Drache forcen")
    severity: str       # "info" | "warn" | "alert"
    category: str       # "objective" | "tempo" | "safety" | "lane"


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
    )


# Rule registry — extend by appending a function. Order doesn't affect
# ``evaluate``'s output (caller sorts by severity).
ALL_RULES: tuple[Callable[["LcdaSnapshot"], Recommendation | None], ...] = (
    rule_drake_priority,
    rule_drake_give_up,
    rule_gold_lead_push,
    rule_far_behind_safe,
    rule_level_deficit,
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
