"""Meta-state rules — gold-lead, gold-deficit, level-deficit, late-game.

These four rules each take a single snapshot-level signal (team gold
diff, avg level diff, or game-time) and surface a coaching call. They
fire as broad-stroke advice when more specific rules don't have a
matching trigger.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import (
    GOLD_DEFICIT_THRESHOLD,
    GOLD_LEAD_THRESHOLD,
    LATE_GAME_S,
    LEVEL_GAP_THRESHOLD,
    Recommendation,
    _avg_level_diff,
    _team_gold_diff,
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
