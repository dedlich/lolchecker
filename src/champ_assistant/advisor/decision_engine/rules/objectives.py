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
    BARON_SETUP_WINDOW_S,
    DRAGON_SOUL_SIGNAL_S,
    DRAKE_PRIORITY_WINDOW_S,
    DRAKE_SETUP_WINDOW_S,
    ELDER_BUFF_EXPIRY_ALERT_S,
    ENEMY_SOUL_POINT_HANDOFF_S,
    GOLD_DEFICIT_THRESHOLD,
    GOLD_LEAD_THRESHOLD,
    HERALD_LATE_GAME_S,
    JUNGLER_DOWN_MIN_S,
    JUNGLER_DOWN_OBJ_WINDOW_S,
    LATE_GAME_S,
    Recommendation,
    VOID_GRUB_HORNGUARD,
    VOID_GRUB_WINDOW_END_S,
    VOID_GRUB_WINDOW_START_S,
    _DRAKE_DISPLAY,
    _active_player,
    _alive_count,
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
    _team_id_set,
)
from .._state import _OBJECTIVE_TAKEN_HYSTERESIS


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
        text=f"Drache ({int(remaining)}s) abgeben — Side-Wellen pushen",
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
        text=f"Baron in {int(remaining)}s — Pinks setzen, Ults checken",
        severity="alert",
        category="objective",
        confidence=0.88,
        risk="MEDIUM",
        ttl_s=remaining,
        reasons=(
            f"Baron spawnt in {int(remaining)}s",
            f"Team-Gold-Diff: {gold:+d}",
            "Baron-Buff = Game-Winner — Setup-Phase kritisch",
            "Side-Wellen prep, Vision sweep",
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


# ─── Multi-phase window rules ──────────────────────────────────────────────


def rule_dragon_window(snapshot: "LcdaSnapshot") -> Recommendation | None:
    """Pro-level Dragon call. Factors: timer, stack count + soul-point,
    drake type, dead-enemy free-window, gold/numbers. Replaces the
    simpler rule_drake_priority + rule_drake_give_up in ALL_RULES.
    Elder Dragon is handled by rule_elder_window — deferred here."""
    remaining = _objective_remaining(snapshot, "Dragon")
    if remaining is None or remaining > DRAKE_SETUP_WINDOW_S:
        return None

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
    soul_point = ally_stacks >= 3
    enemy_soul_point = enemy_stacks >= 3

    drake_obj = next(
        (o for o in (getattr(snapshot, "objectives", []) or [])
         if getattr(o, "name", "") == "Dragon"),
        None,
    )
    if getattr(drake_obj, "detail", None) == "Elder":
        return None

    drake_name = _DRAKE_DISPLAY.get(
        getattr(drake_obj, "detail", None) or "", "Drache"
    )

    dead_enemies = [e for e in enemies if not getattr(e, "is_alive", True)]
    free_window = numbers_diff > 0 and len(dead_enemies) > 0

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
        return None

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


# ─── Post-kill conversion + setup-window rules ─────────────────────────────

OBJECTIVE_TAKEN_RECENT_S: float = 20.0

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
        if (drake_detail or "").lower() == "elder":
            return (
                "ELDER taken — 3-min Execute aktiv, INHIB JETZT, kein Wait",
                "alert", "LOW", 30.0, 0.92, "objective_taken_elder",
            )
        if ally_drake_count >= 4:
            return (
                "SOUL taken — permanenter Buff. Baron oder Inhib forcen.",
                "alert", "LOW", 30.0, 0.90, "objective_taken_soul",
            )
        return (
            "Drache taken — nächste Drake setup, Vision pflanzen",
            "info", "LOW", 25.0, 0.80, "objective_taken_drake",
        )
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


# Setup-window thresholds.
# 90s — earliest you'd start prepping. Beyond this is too far out to influence
# the wave that'll crash at spawn. 30s — last possible push; below 30s the
# wave is locked in and the dragon/baron-window rules cover the actual fight.
SETUP_WINDOW_MIN_S: float = 30.0
SETUP_WINDOW_MAX_S: float = 90.0

_OBJECTIVE_PRIORITY: dict[str, int] = {
    "Baron":     4,
    "Dragon":    3,
    "Herald":    2,
    "VoidGrubs": 1,
}

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
        active_position == "UTILITY" and pit_side == "BOTTOM"
    )

    if objective == "Dragon":
        if near_pit:
            return "Welle pushen, Pixel-Buschwerk warden, Pit-Control"
        if active_position == "MIDDLE":
            return "Mid-Welle shoven, dann rotieren, Vision setzen"
        if active_position == "JUNGLE":
            return "Pit + Drachen-Buschwerk warden, Smite ready"
        return "Welle hard pushen, TP bereithalten, dann rotieren"

    if objective == "Baron":
        if near_pit:
            return "Pit-Vision setzen, Welle pushen, Tank-Position"
        if active_position == "MIDDLE":
            return "Mid pushen + River-Vision, dann zum Pit"
        if active_position == "JUNGLE":
            return "Pit + River warden, Smite ready, Vision-Sweeper"
        return "Bot-Welle resetten, dann zum Pit gruppieren"

    if objective == "Herald":
        if near_pit:
            return "Welle pushen, River-Buschwerk warden, Pit-Control"
        if active_position == "MIDDLE":
            return "Mid pushen, dann zum Top-River rotieren"
        if active_position == "JUNGLE":
            return "Pit + Top-River warden, Smite ready"
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

    best: tuple[int, float, str] | None = None
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
