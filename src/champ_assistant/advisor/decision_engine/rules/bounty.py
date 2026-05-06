"""Bounty-awareness rules (Charter B5 — focus-call coaching).

Four-leg bounty matrix:
  * ``rule_active_bounty``         — "you have bounty"      (defensive)
  * ``rule_enemy_bounty``          — "enemy has bounty"     (offensive focus)
  * ``rule_ally_bounty``           — "ally has bounty"      (protect-the-carry)
  * ``rule_objective_bounty_active`` — Riot's catch-up gold mechanic notice

The personal/enemy/ally streak rules share the BOUNTY_TIER_* tier
ladder; ``objective_bounty_active`` uses its own gold-differential
phase thresholds.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import Recommendation, _kill_streak, _team_gold_diff
from .._state import (
    _ALLY_BOUNTY_HYSTERESIS,
    _BOUNTY_HYSTERESIS,
    _ENEMY_BOUNTY_HYSTERESIS,
    _OBJECTIVE_BOUNTY_HYSTERESIS,
)


def _active_player(snapshot: "LcdaSnapshot") -> object | None:
    allies = list(getattr(snapshot, "allies", []) or [])
    sn = getattr(snapshot, "active_summoner", "") or ""
    for a in allies:
        if str(getattr(a, "summoner_name", "") or "") == sn:
            return a
    return None


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

    deaths = int(getattr(active, "deaths", 0) or 0)
    h = _BOUNTY_HYSTERESIS
    if deaths > h.last_seen_deaths:
        h.reset()
        h.last_seen_deaths = deaths

    streak = _kill_streak(active, list(getattr(snapshot, "raw_events", []) or []))
    if streak < BOUNTY_TIER_INFO_S:
        return None

    if streak >= BOUNTY_TIER_GODLIKE_S:
        tier = BOUNTY_TIER_GODLIKE_S
    elif streak >= BOUNTY_TIER_WARN_S:
        tier = BOUNTY_TIER_WARN_S
    else:
        tier = BOUNTY_TIER_INFO_S
    if tier <= h.last_fired_tier:
        return None
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

    best: tuple[int, int, str, object] | None = None
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


# ─── Objective bounty (Riot catch-up gold) thresholds ───────────────────────
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

    abs_diff = abs(diff)
    if abs_diff < OBJECTIVE_BOUNTY_REARM_THRESHOLD:
        h.fired_behind = False
        h.fired_ahead = False
        return None

    if abs_diff < OBJECTIVE_BOUNTY_DIFF_THRESHOLD:
        return None

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
