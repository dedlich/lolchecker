"""Combat / numbers / fight-state rules.

Ten rules that fire on team-fight outcomes, numbers (alive count) deltas,
fight-score signals, ace detection, kill-lead snowballing, first blood,
and shutdown conversion. Several share the bounty-tier ladder defined in
``_core`` (``BOUNTY_TIER_INFO_S`` / ``WARN`` / ``GODLIKE``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import (
    BOUNTY_TIER_GODLIKE_S,
    BOUNTY_TIER_INFO_S,
    BOUNTY_TIER_WARN_S,
    FIGHT_SCORE_THRESHOLD,
    FIGHT_WINDOW_CLOSING_S,
    GOLD_LEAD_THRESHOLD,
    KILL_DEFICIT_THRESHOLD,
    KILL_LEAD_THRESHOLD,
    Recommendation,
    _alive_count,
    _aoe_cc_warnings,
    _focus_target,
    _kill_streak,
    _team_gold_diff,
    _team_id_set,
    _team_kill_diff,
    fight_score,
    win_probability,
)
from .._state import (
    _FIRST_BLOOD_HYSTERESIS,
    _SHUTDOWN_TAKEN_HYSTERESIS,
    _TEAMFIGHT_OUTCOME_HYSTERESIS,
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
        return None
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
        if numbers_diff < 0:
            return None
        severity = "alert" if score >= 0.55 or numbers_diff >= 2 else "warn"
        confidence = min(0.95, 0.60 + score * 0.35)
        risk = "LOW" if gold >= GOLD_LEAD_THRESHOLD else "MEDIUM"

        parts: list[str] = []
        if numbers_diff >= 1:
            parts.append(f"Fight {allies_alive}v{enemies_alive} ({win_pct}%)")
        else:
            parts.append(f"Fight JETZT ({win_pct}%)")
        if focus:
            parts.append(f"Fokus {focus[0]}")
        if aoe_warnings:
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

    has_enemy_respawn = any(
        getattr(e, "respawn_timer", None) is not None for e in enemies
    )
    if not has_enemy_respawn:
        return None

    allies_alive = _alive_count(allies, default_to_full_team=False)
    enemies_alive = _alive_count(enemies, default_to_full_team=False)
    if allies_alive <= enemies_alive:
        return None

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
TEAMFIGHT_WINDOW_S: float = 15.0
TEAMFIGHT_MIN_TOTAL_KILLS: int = 3
TEAMFIGHT_DECISIVE_NET: int = 2
TEAMFIGHT_LOPSIDED_NET: int = 3


def _teamfight_outcome_advice(game_time: float, ally_won: bool) -> str:
    """Phase-aware "what to do in the next 30 s" line. Pros adjust by phase:
    early-game wins press for plates, mid-game wins force baron/drake,
    late-game wins force inhibs/elder."""
    if ally_won:
        if game_time < 840.0:
            return "Plates + Drache forcen, Wave-Pressure, kein Solo-Trade"
        if game_time < 1500.0:
            return "Drache/Baron forcen, Vision in ihrem Jungle, Tower"
        return "Baron / Elder forcen, Inhib pushen, kein Solo-Splitten"
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

    allies = list(getattr(snapshot, "allies", []) or [])
    enemies = list(getattr(snapshot, "enemies", []) or [])
    ally_ids = _team_id_set(allies)
    enemy_ids = _team_id_set(enemies)
    if not ally_ids or not enemy_ids:
        return None

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
        return None

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
    if game_time < 840.0:
        return "Plates + Drache forcen, Wave-Pressure" if not big else \
               "Plates + Drache + Tower hard pushen — Tempo-Reset"
    if game_time < 1500.0:
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

    for e in enemies:
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        if not getattr(e, "is_alive", True):
            continue
        current_tier = _streak_to_tier(_kill_streak(e, events))
        h.last_alive_tier[name] = max(
            h.last_alive_tier.get(name, 0), current_tier,
        )

    best: tuple[int, int, str] | None = None
    for e in enemies:
        if getattr(e, "is_alive", True):
            continue
        name = str(getattr(e, "champion_name", "") or "")
        if not name:
            continue
        deaths = int(getattr(e, "deaths", 0) or 0)
        if deaths <= h.fired_for_death.get(name, -1):
            continue
        pre_death_tier = h.last_alive_tier.get(name, 0)
        if pre_death_tier < BOUNTY_TIER_INFO_S:
            continue
        candidate = (pre_death_tier, deaths, name)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    tier, deaths, name = best
    h.fired_for_death[name] = deaths
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
