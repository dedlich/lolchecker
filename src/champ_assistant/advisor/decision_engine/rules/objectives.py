"""Objective rules — drakes, baron, herald, void grubs, dragon soul.

This is the largest domain in the decision engine: every rule that
fires on the spawn / kill / buff state of a neutral objective lives
here. Window-rules with multi-phase logic (``rule_dragon_window``,
``rule_elder_window``, ``rule_baron_window``) and the
post-kill / setup helpers move in later commits — this file currently
holds the simpler "spawn-soon" priority rules, herald-flow rules,
soul/grubs/jungler-down rules, and the four buff rules.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....lcda.source import LcdaSnapshot

from .._core import (
    BARON_BUFF_EXPIRY_ALERT_S,
    BARON_PRIORITY_WINDOW_S,
    DRAGON_SOUL_SIGNAL_S,
    DRAKE_PRIORITY_WINDOW_S,
    ELDER_BUFF_EXPIRY_ALERT_S,
    ENEMY_SOUL_POINT_HANDOFF_S,
    GOLD_DEFICIT_THRESHOLD,
    GOLD_LEAD_THRESHOLD,
    HERALD_LATE_GAME_S,
    JUNGLER_DOWN_MIN_S,
    JUNGLER_DOWN_OBJ_WINDOW_S,
    VOID_GRUB_HORNGUARD,
    VOID_GRUB_WINDOW_END_S,
    VOID_GRUB_WINDOW_START_S,
    Recommendation,
    _ally_baron_buff_remaining,
    _ally_elder_buff_remaining,
    _ally_grub_count,
    _avg_level_diff,
    _drake_stack_count,
    _enemy_baron_buff_remaining,
    _enemy_drake_stack_count,
    _enemy_elder_buff_remaining,
    _enemy_grub_count,
    _enemy_herald_pickup,
    _herald_pickup,
    _is_jungler,
    _objective_remaining,
    _player_ids,
    _team_gold_diff,
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
        return None
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


def rule_baron_priority(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Baron up soon AND we have resources → set up vision + group.
    Baron's window is wider than drake (45s vs 30s) because the prep
    matters more — wave clear, vision sweep, ult availability check."""
    remaining = _objective_remaining(snapshot, "Baron")
    if remaining is None or remaining > BARON_PRIORITY_WINDOW_S:
        return None
    gold = _team_gold_diff(snapshot)
    if gold < -GOLD_LEAD_THRESHOLD:
        return None
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
        return None
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
